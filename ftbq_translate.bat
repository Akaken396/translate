@echo off
setlocal
rem ============================================================
rem  FTB Quests translator - drag and drop your quests folder
rem  (or the instance folder) onto this file.
rem
rem  To use DeepL, paste your DeepL API key after the = below.
rem ============================================================
set "DEEPL_KEY_HERE="

if not "%DEEPL_KEY_HERE%"=="" set "DEEPL_AUTH_KEY=%DEEPL_KEY_HERE%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if "%~1"=="" (
  echo Drag and drop the FTB Quests folder onto this file.
  pause
  exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0ftbq_translate.py" "%~1"
) else (
  python "%~dp0ftbq_translate.py" "%~1"
)
pause
