#!/usr/bin/env python3
"""FTB Quests (Minecraft 1.20.1 / FTB Quests 2001.x) quest translator.

Give it an FTB Quests quest folder (config/ftbquests/quests, or an instance /
config folder that contains it). It will

  1. replace every quest text (titles, subtitles, descriptions, ...) in the
     .snbt files with a translation key such as "{ftbqtr.1A2B3C4D5E6F7A8B.title}",
  2. machine-translate the original texts (DeepL or Google Translate),
  3. write a resource pack containing en_us.json (original text) and
     ja_jp.json (translation) for those keys.

Only the Python standard library is used (Python 3.8+).
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from xml.sax.saxutils import escape as xml_escape, unescape as xml_unescape

# ---------------------------------------------------------------------------
# SNBT parser that keeps the source position of every string value, so the
# files can be rewritten in place without touching their formatting.
# ---------------------------------------------------------------------------

ESCAPES = {'"': '"', "'": "'", '\\': '\\', 't': '\t', 'b': '\b', 'n': '\n', 'r': '\r', 'f': '\f'}


class SnbtError(Exception):
    pass


class Str:
    __slots__ = ('value', 'start', 'end', 'quoted')

    def __init__(self, value, start, end, quoted):
        self.value = value
        self.start = start
        self.end = end
        self.quoted = quoted


class Compound:
    __slots__ = ('entries',)

    def __init__(self):
        self.entries = []

    def get(self, key):
        for k, v in self.entries:
            if k == key:
                return v
        return None


class ListNode:
    __slots__ = ('items',)

    def __init__(self):
        self.items = []


class Other:
    __slots__ = ()


OTHER = Other()


def is_simple_char(c):
    return c.isalpha() or c.isdigit() or c in '._-+∞'


class SnbtParser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    def error(self, msg):
        line = self.s.count('\n', 0, self.i) + 1
        raise SnbtError(f'{msg} (line {line})')

    def next_ns(self):
        while self.i < self.n and self.s[self.i] <= ' ':
            self.i += 1
        if self.i >= self.n:
            self.error('unexpected end of file')
        c = self.s[self.i]
        self.i += 1
        return c

    def parse(self):
        node = self.read_tag(self.next_ns())
        return node

    def read_tag(self, first):
        if first == '{':
            return self.read_compound()
        if first == '[':
            return self.read_collection()
        if first in '"\'':
            return self.read_quoted(first)
        start = self.i - 1
        word = self.read_word(first)
        return Str(word, start, self.i, False)

    def read_word(self, first):
        if not is_simple_char(first):
            self.error(f'unexpected character {first!r}')
        start = self.i - 1
        while self.i < self.n and is_simple_char(self.s[self.i]):
            self.i += 1
        return self.s[start:self.i]

    def read_quoted(self, stop):
        start = self.i - 1
        buf = []
        escape = False
        while True:
            if self.i >= self.n:
                self.error('unterminated string')
            c = self.s[self.i]
            self.i += 1
            if c == '\n':
                self.error('new line inside string')
            if escape:
                escape = False
                if c in ESCAPES:
                    buf.append(ESCAPES[c])
            elif c == '\\':
                escape = True
            elif c == stop:
                return Str(''.join(buf), start, self.i, True)
            else:
                buf.append(c)

    def read_compound(self):
        node = Compound()
        while True:
            c = self.next_ns()
            if c == '}':
                return node
            if c == ',':
                continue
            if c in '"\'':
                key = self.read_quoted(c).value
            else:
                key = self.read_word(c)
            sep = self.next_ns()
            if sep not in ':=':
                self.error(f"expected ':' after key {key!r}")
            node.entries.append((key, self.read_tag(self.next_ns())))

    def read_collection(self):
        save = self.i
        a = self.next_ns()
        b = self.next_ns()
        if b == ';' and a in 'IiLlBb':
            while True:
                c = self.next_ns()
                if c == ']':
                    return OTHER
                if c != ',':
                    self.read_word(c)
        self.i = save
        node = ListNode()
        while True:
            c = self.next_ns()
            if c == ']':
                return node
            if c == ',':
                continue
            node.items.append(self.read_tag(c))


# ---------------------------------------------------------------------------
# Collecting quest texts
# ---------------------------------------------------------------------------

JSON_TEXT_RE = re.compile(r'^[{\[]\s*"')
KEY_ONLY_RE = re.compile(r'^\{[^{}:]+\}$')
PAGEBREAK = '{@pagebreak}'
# Things that must survive translation untouched: escapes, colour codes,
# {substitutions} and line breaks.
TOKEN_RE = re.compile(r'\\.|&#[0-9A-Fa-f]{6}|[&§][0-9A-Fa-fK-Ok-oRr]|\{[^{}]*\}|\n')
CJK_RE = re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')

# Lists of child objects that carry their own texts. We only recurse into
# these so item NBT (e.g. written books with a "title") is never touched.
CHILD_LISTS = ('quests', 'tasks', 'rewards', 'chapter_groups')


def has_text(s):
    return any(ch.isalpha() for ch in TOKEN_RE.sub('', s))


def sanitize_id(s):
    return re.sub(r'[^A-Za-z0-9_]', '_', s)


class Entry:
    __slots__ = ('key', 'source', 'node', 'bare')

    def __init__(self, key, source, node, bare):
        self.key = key
        self.source = source
        self.node = node
        self.bare = bare


class Collector:
    def __init__(self, prefix):
        self.prefix = prefix
        self.entries = []
        self.skipped = []

    def add(self, owner, field, idx, node, bare=False, where=''):
        if not isinstance(node, Str):
            return
        text = node.value
        label = f'{where} {owner}.{field}' + (f'[{idx}]' if idx is not None else '')
        if not text.strip() or text == PAGEBREAK:
            return
        if KEY_ONLY_RE.match(text):
            self.skipped.append((label, 'already a translation key', text))
            return
        if JSON_TEXT_RE.match(text):
            self.skipped.append((label, 'raw JSON text is not supported', text))
            return
        if not has_text(text):
            return
        key = f'{self.prefix}.{owner}.{field}' + (f'.{idx}' if idx is not None else '')
        self.entries.append(Entry(key, text, node, bare))

    def walk(self, comp, owner, where):
        oid = comp.get('id')
        if isinstance(oid, Str) and oid.value:
            owner = sanitize_id(oid.value)
        for k, v in comp.entries:
            if k in ('title', 'lock_message') or (k == 'subtitle' and isinstance(v, Str)):
                self.add(owner, k, None, v, where=where)
            elif k in ('subtitle', 'description') and isinstance(v, ListNode):
                for i, item in enumerate(v.items):
                    self.add(owner, k, i, item, where=where)
            elif k == 'images' and isinstance(v, ListNode):
                for i, img in enumerate(v.items):
                    if isinstance(img, Compound):
                        hover = img.get('hover')
                        if isinstance(hover, ListNode):
                            for j, item in enumerate(hover.items):
                                # Image hover lines are read as plain translation keys.
                                self.add(f'{owner}.image{i}', 'hover', j, item, bare=True, where=where)
            elif k in CHILD_LISTS and isinstance(v, ListNode):
                for item in v.items:
                    if isinstance(item, Compound):
                        self.walk(item, owner, where)


# ---------------------------------------------------------------------------
# Translation engines
# ---------------------------------------------------------------------------

def http(url, data=None, headers=None, retries=5):
    delay = 2
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:300]
            if e.code in (403, 456) or attempt == retries - 1:
                raise RuntimeError(f'HTTP {e.code}: {body}') from None
            if e.code not in (429, 500, 502, 503, 504, 529):
                raise RuntimeError(f'HTTP {e.code}: {body}') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries - 1:
                raise RuntimeError(f'network error: {e}') from None
        time.sleep(delay)
        delay *= 2


class DeepL:
    name = 'DeepL'
    batch = 40

    def __init__(self, key, source, target):
        self.key = key
        host = 'api-free.deepl.com' if key.endswith(':fx') else 'api.deepl.com'
        self.url = f'https://{host}/v2/translate'
        self.source = source.upper() if source and source != 'auto' else None
        self.target = target.upper()

    def mark(self, i):
        return f'<x i="{i}"/>'

    def encode(self, text):
        return xml_escape(text)

    def translate(self, texts):
        body = {'text': texts, 'target_lang': self.target, 'tag_handling': 'xml', 'ignore_tags': ['x']}
        if self.source:
            body['source_lang'] = self.source
        res = http(self.url, json.dumps(body).encode('utf-8'),
                   {'Authorization': f'DeepL-Auth-Key {self.key}', 'Content-Type': 'application/json'})
        return [t['text'] for t in json.loads(res)['translations']]

    def decode(self, text):
        return xml_unescape(text)

    MARK_RE = re.compile(r'<x\s+i="(\d+)"\s*/>')


class Google:
    name = 'Google Translate'
    batch = 1

    def __init__(self, source, target):
        self.source = source or 'auto'
        self.target = target

    def mark(self, i):
        return f'[[{i}]]'

    def encode(self, text):
        return text

    def translate(self, texts):
        out = []
        for text in texts:
            q = urllib.parse.urlencode({'client': 'gtx', 'sl': self.source, 'tl': self.target, 'dt': 't', 'q': text})
            res = json.loads(http('https://translate.googleapis.com/translate_a/single?' + q,
                                  headers={'User-Agent': 'Mozilla/5.0'}))
            out.append(''.join(part[0] for part in res[0] if part and part[0]))
            time.sleep(0.2)
        return out

    def decode(self, text):
        return text

    MARK_RE = re.compile(r'\[\[\s*(\d+)\s*\]\]')


class NoTranslate:
    name = 'none (copy the original text)'


def clean_translated(text):
    # Characters that FTB Quests would read as formatting codes or
    # substitutions must not appear in translated plain text.
    return text.replace('&', '＆').replace('§', '').replace('{', '｛').replace('}', '｝').replace('\\', '＼')


def split_parts(text):
    """Split into [(is_token, str)]."""
    parts = []
    pos = 0
    for m in TOKEN_RE.finditer(text):
        if m.start() > pos:
            parts.append((False, text[pos:m.start()]))
        parts.append((True, m.group()))
        pos = m.end()
    if pos < len(text):
        parts.append((False, text[pos:]))
    return parts


class Job:
    """One source string: the protected prefix / suffix are kept as is, the
    middle part goes to the translator with placeholders for tokens."""

    def __init__(self, source):
        self.source = source
        parts = split_parts(source)
        first = next((i for i, (t, s) in enumerate(parts) if not t and s.strip()), None)
        last = max(i for i, (t, s) in enumerate(parts) if not t and s.strip())
        self.prefix = ''.join(s for _, s in parts[:first])
        self.suffix = ''.join(s for _, s in parts[last + 1:])
        self.middle = parts[first:last + 1]
        # keep surrounding whitespace of the middle part
        lead = self.middle[0][1]
        trail = self.middle[-1][1]
        self.lead_ws = lead[:len(lead) - len(lead.lstrip())]
        self.trail_ws = trail[len(trail.rstrip()):]
        self.middle[0] = (False, lead.lstrip())
        self.middle[-1] = (False, self.middle[-1][1].rstrip())
        self.tokens = [s for t, s in self.middle if t]

    def masked(self, engine):
        out = []
        n = 0
        for t, s in self.middle:
            if t:
                out.append(engine.mark(n))
                n += 1
            else:
                out.append(engine.encode(s))
        return ''.join(out)

    def unmask(self, engine, translated):
        text = engine.decode(translated) if not self.tokens else translated
        if self.tokens:
            found = [int(m.group(1)) for m in engine.MARK_RE.finditer(translated)]
            if sorted(found) != list(range(len(self.tokens))):
                return None
            pieces = []
            pos = 0
            for m in engine.MARK_RE.finditer(translated):
                pieces.append(clean_translated(engine.decode(translated[pos:m.start()])))
                pieces.append(self.tokens[int(m.group(1))])
                pos = m.end()
            pieces.append(clean_translated(engine.decode(translated[pos:])))
            text = ''.join(pieces)
        else:
            text = clean_translated(text)
        return self.wrap(text)

    def wrap(self, middle):
        return self.prefix + self.lead_ws + middle.strip() + self.trail_ws + self.suffix

    def segments(self):
        return [s for t, s in self.middle if not t and has_text(s)]

    def from_segments(self, translated):
        it = iter(translated)
        out = []
        for t, s in self.middle:
            if t or not has_text(s):
                out.append(s)
            else:
                lead = s[:len(s) - len(s.lstrip())]
                trail = s[len(s.rstrip()):]
                out.append(lead + clean_translated(next(it).strip()) + trail)
        return self.wrap(''.join(out))


def run_translation(engine, sources, cache, cache_path, target):
    todo = []
    for s in sources:
        if s in cache:
            continue
        if target.lower().startswith('ja') and CJK_RE.search(s):
            cache[s] = s  # already Japanese
            continue
        todo.append(s)
    if isinstance(engine, NoTranslate):
        # Put the original text in the cache so it can be translated by hand.
        for s in todo:
            cache[s] = s
        todo = []
    if not todo:
        save_json(cache_path, cache)
        return

    print(f'{engine.name} で {len(todo)} 件を翻訳します...')
    jobs = [Job(s) for s in todo]
    done = 0
    last_save = time.time()
    for b in range(0, len(jobs), engine.batch):
        chunk = jobs[b:b + engine.batch]
        results = engine.translate([j.masked(engine) for j in chunk])
        retry = []
        for job, res in zip(chunk, results):
            text = job.unmask(engine, res)
            if text is None:
                retry.append(job)
            else:
                cache[job.source] = text
        # Placeholders got lost: translate the pieces between codes separately.
        for job in retry:
            segs = job.segments()
            plain = [engine.encode(s.strip()) for s in segs]
            res = engine.translate(plain) if plain else []
            cache[job.source] = job.from_segments([engine.decode(r) for r in res])
        done += len(chunk)
        print(f'  {done}/{len(jobs)}', end='\r', flush=True)
        if time.time() - last_save > 10:
            save_json(cache_path, cache)
            last_save = time.time()
    print()
    save_json(cache_path, cache)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def find_quest_dir(path):
    path = os.path.abspath(path)
    for cand in (path, os.path.join(path, 'quests'), os.path.join(path, 'ftbquests', 'quests'),
                 os.path.join(path, 'config', 'ftbquests', 'quests')):
        if os.path.isdir(os.path.join(cand, 'chapters')):
            return cand
    return None


def lang_value(text):
    # I18n.get() runs String.format, so a bare % would break the text.
    return text.replace('%', '%%')


def main():
    ap = argparse.ArgumentParser(description='FTB Quests (1.20.1) のクエストを翻訳キー化し、翻訳リソースパックを作ります。')
    ap.add_argument('quests', help='クエストフォルダ (config/ftbquests/quests) か、それを含むフォルダ')
    ap.add_argument('-o', '--out', help='出力先フォルダ (既定: <クエストフォルダ>_translated)')
    ap.add_argument('-e', '--engine', choices=['auto', 'deepl', 'google', 'none'], default='auto',
                    help='翻訳エンジン。auto は DeepL キーがあれば DeepL、無ければ Google')
    ap.add_argument('--deepl-key', default=os.environ.get('DEEPL_AUTH_KEY', ''), help='DeepL API キー (環境変数 DEEPL_AUTH_KEY でも可)')
    ap.add_argument('--source-lang', default='auto', help='原文の言語 (既定: auto)')
    ap.add_argument('--target-lang', default='ja', help='翻訳先の言語 (既定: ja)')
    ap.add_argument('--lang-file', default='ja_jp', help='出力する言語ファイル名 (既定: ja_jp)')
    ap.add_argument('--prefix', default='ftbqtr', help='翻訳キーの接頭辞 (既定: ftbqtr)')
    ap.add_argument('--pack-name', default='FTBQuests-Translation', help='リソースパック名')
    args = ap.parse_args()

    src = find_quest_dir(args.quests)
    if not src:
        sys.exit(f'エラー: {args.quests} にクエストフォルダ (chapters フォルダを含む) が見つかりません。')
    out = os.path.abspath(args.out or (src.rstrip(os.sep) + '_translated'))
    if os.path.commonpath([out, src]) == src:
        sys.exit('エラー: 出力先をクエストフォルダの中にはできません。')
    print(f'クエストフォルダ: {src}')
    print(f'出力先        : {out}')
    os.makedirs(out, exist_ok=True)

    # 1. parse every .snbt file and collect texts
    col = Collector(sanitize_id(args.prefix))
    files = {}
    for root, _, names in os.walk(src):
        for name in sorted(names):
            if not name.endswith('.snbt'):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, src)
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                text = f.read()
            try:
                tree = SnbtParser(text).parse()
            except SnbtError as e:
                sys.exit(f'エラー: {rel} を読めませんでした: {e}')
            before = len(col.entries)
            if isinstance(tree, Compound):
                col.walk(tree, sanitize_id(os.path.splitext(rel)[0].replace(os.sep, '.')), rel)
            files[rel] = (text, col.entries[before:])

    keys = {}
    for e in col.entries:
        if e.key in keys and keys[e.key] != e.source:
            n = 2
            while f'{e.key}_{n}' in keys:
                n += 1
            e.key = f'{e.key}_{n}'
        keys[e.key] = e.source
    print(f'翻訳対象のテキスト: {len(col.entries)} 件 ({len(set(keys.values()))} 種類)')
    if not col.entries:
        sys.exit('翻訳するテキストが見つかりませんでした (すでに翻訳キー化されている可能性があります)。')

    # 2. translate
    cache_path = os.path.join(out, 'translation_cache.json')
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        print(f'翻訳キャッシュを読み込みました: {len(cache)} 件')

    engine_name = args.engine
    if engine_name == 'auto':
        engine_name = 'deepl' if args.deepl_key else 'google'
    if engine_name == 'deepl':
        if not args.deepl_key:
            sys.exit('エラー: DeepL を使うには --deepl-key か環境変数 DEEPL_AUTH_KEY が必要です。')
        engine = DeepL(args.deepl_key, args.source_lang, args.target_lang)
    elif engine_name == 'google':
        engine = Google(args.source_lang, args.target_lang)
    else:
        engine = NoTranslate()

    try:
        run_translation(engine, list(dict.fromkeys(keys.values())), cache, cache_path, args.target_lang)
    except (RuntimeError, KeyboardInterrupt) as e:
        save_json(cache_path, cache)
        print(f'\n翻訳が中断されました: {e}')
        print('ここまでの翻訳は保存しました。もう一度実行すると続きから再開します。')
        sys.exit(1)

    # 3. write converted quest folder
    qout = os.path.join(out, 'quests')
    if os.path.isdir(qout):
        shutil.rmtree(qout)
    shutil.copytree(src, qout)
    for rel, (text, entries) in files.items():
        if not entries:
            continue
        for e in sorted(entries, key=lambda e: e.node.start, reverse=True):
            lit = e.key if e.bare else '{' + e.key + '}'
            text = text[:e.node.start] + '"' + lit + '"' + text[e.node.end:]
        with open(os.path.join(qout, rel), 'w', encoding='utf-8', newline='') as f:
            f.write(text)

    # 4. resource pack
    en = {k: lang_value(v) for k, v in keys.items()}
    tr = {k: lang_value(cache.get(v, v)) for k, v in keys.items()}
    untranslated = sum(1 for v in keys.values() if v not in cache)
    pack_path = os.path.join(out, args.pack_name + '.zip')
    mcmeta = {'pack': {'pack_format': 15, 'description': f'FTB Quests translation ({args.lang_file})'}}
    with zipfile.ZipFile(pack_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('pack.mcmeta', json.dumps(mcmeta, ensure_ascii=False, indent=2))
        z.writestr('assets/ftbquests_translation/lang/en_us.json', json.dumps(en, ensure_ascii=False, indent=2))
        z.writestr(f'assets/ftbquests_translation/lang/{args.lang_file}.json', json.dumps(tr, ensure_ascii=False, indent=2))

    with open(os.path.join(out, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(f'翻訳キー化したテキスト: {len(col.entries)} 件\n')
        f.write(f'未翻訳 (原文のまま): {untranslated} 件\n')
        f.write(f'スキップしたテキスト: {len(col.skipped)} 件\n\n')
        for label, why, text in col.skipped:
            f.write(f'[{why}] {label}\n    {text}\n')

    print()
    print('完了しました。')
    print(f'  リソースパック : {pack_path}')
    print(f'  クエストフォルダ: {qout}')
    if untranslated:
        print(f'  ※ {untranslated} 件は翻訳されず原文のままです。')
    if col.skipped:
        print(f'  ※ {len(col.skipped)} 件はスキップしました (report.txt を参照)。')
    print()
    print('導入方法:')
    print('  1. ゲーム/サーバーの config/ftbquests/quests を出力された quests フォルダで置き換える (元のフォルダはバックアップ)')
    print('  2. リソースパックの zip を resourcepacks フォルダに入れて有効にする')


if __name__ == '__main__':
    main()
