@echo off
rem Talk Autopsy web page: starts the local server (this computer only) and opens the page in the
rem browser. Closing this window stops the server. Started a second time while the page runs, it
rem opens the running page again instead of starting another server.
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
title Talk Autopsy - web page (close this window to stop)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
uv run --quiet talk-autopsy web %*
set "TA_CODE=%errorlevel%"
if not "%TA_CODE%"=="0" pause
endlocal & exit /b %TA_CODE%
