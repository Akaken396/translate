# FTB Quests 翻訳ツール (Minecraft 1.20.1 / Forge 47.x)

FTB Quests のクエストフォルダを渡すと、クエストの文章を機械翻訳して **リソースパック** にするツールです。
FTB Quests 2001.x (1.20.1) 向けです。

## しくみ

1.20.1 の FTB Quests はクエストの文章を `.snbt` に直接書き込んでいるため、リソースパックだけでは差し替えられません。
そこでこのツールは次の 2 つを出力します。

| 出力 | 内容 |
| --- | --- |
| `quests/` | 文章を翻訳キー (`{ftbqtr.<ID>.title}` など) に置き換えたクエストフォルダ |
| `FTBQuests-Translation.zip` | `en_us.json` (原文) と `ja_jp.json` (翻訳) が入ったリソースパック |

英語のままにすると原文が表示され、日本語にすると翻訳が表示されます。
`&e` などのカラーコード、`{@pagebreak}`、`{image:...}` などはそのまま残ります。

## 必要なもの

* Python 3.8 以上 (https://www.python.org/ からインストール。追加のライブラリは不要)

## 使い方 (Windows)

1. このフォルダ (`ftbq_translate.py` と `ftbq_translate.bat`) を好きな場所に置く。
2. `config/ftbquests/quests` フォルダ (またはインスタンスのフォルダ) を `ftbq_translate.bat` にドラッグ＆ドロップする。
3. 終わると `quests_translated` フォルダが `quests` の隣にできる。

コマンドで実行する場合:

```
python ftbq_translate.py "C:\path\to\.minecraft\config\ftbquests\quests"
```

## 導入方法

1. 元の `config/ftbquests/quests` を **バックアップ** し、出力された `quests_translated/quests` で置き換える。
   * マルチプレイの場合はクエストがサーバーから送られるため、**サーバー側** の quests フォルダを置き換えます。
2. `FTBQuests-Translation.zip` をクライアントの `resourcepacks` フォルダに入れ、ゲーム内で有効にする。
   (KubeJS がある場合は、zip の中の `assets` フォルダを `kubejs/assets` に入れても読み込まれます)

## 翻訳エンジン

| エンジン | 指定 | 備考 |
| --- | --- | --- |
| Google 翻訳 | `-e google` (キーが無いときの既定) | API キー不要。件数が多いと一時的に制限されることがある |
| DeepL | `-e deepl --deepl-key <キー>` | 精度が高くおすすめ。無料版 API キー (月 50 万文字) でも可 |
| 翻訳しない | `-e none` | 原文をそのまま `translation_cache.json` と `ja_jp.json` に入れる (手で翻訳して再実行する用) |

`.bat` で DeepL を使うときは、`ftbq_translate.bat` をメモ帳で開いて `set "DEEPL_KEY_HERE="` の `=` の後ろにキーを貼り付けます。
環境変数 `DEEPL_AUTH_KEY` でも指定できます。

## 翻訳の修正・再開

* 翻訳結果は出力フォルダの `translation_cache.json` (原文 → 翻訳) に保存されます。
* 途中で止まっても、もう一度実行すると続きから再開します。
* 翻訳を手で直したいときは `translation_cache.json` を編集して再実行すると、リソースパックに反映されます。
* クエストを更新したときも、**元の (翻訳キー化する前の) クエストフォルダ** に対して再実行してください。翻訳済みの文章はキャッシュから再利用されます。

## オプション

```
-o, --out          出力先フォルダ (既定: <クエストフォルダ>_translated)
--target-lang      翻訳先の言語 (既定: ja)
--lang-file        言語ファイル名 (既定: ja_jp)
--prefix           翻訳キーの接頭辞 (既定: ftbqtr)
--pack-name        リソースパックのファイル名
```

## 注意

* 翻訳されるのは、チャプター・クエスト・タスク・報酬・報酬テーブル・チャプターグループのタイトル、サブタイトル、説明文、画像のホバー文、ロックメッセージです。
* JSON 形式で書かれた行 (`["", {"text": ...}]`) と、すでに翻訳キーになっている行は対象外です (`report.txt` に一覧が出ます)。
* 機械翻訳なので、Mod のアイテム名などが不自然に訳されることがあります。必要に応じて `translation_cache.json` で直してください。
