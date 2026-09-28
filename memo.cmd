@echo off
rem Generate a memo draft with Claude. Double-click, or: memo.cmd GOOG "your one-line thesis"
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0backend"
set "TICKER=%~1"
set "THESIS=%~2"
if "%TICKER%"=="" set /p "TICKER=Ticker (e.g. GOOG): "
if "%THESIS%"=="" set /p "THESIS=One-line thesis: "
".venv\Scripts\python.exe" -m investment_ai memo-draft %TICKER% --thesis "%THESIS%"
echo.
pause
