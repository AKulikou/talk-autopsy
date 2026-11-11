@echo off
rem Talk Autopsy launcher: double-click it (or its desktop shortcut), or drop a video file onto it.
rem It runs talk-autopsy.ps1 next to it from the project folder, with PowerShell 7 (pwsh) if
rem installed, else Windows PowerShell. Arguments are passed on (a dropped file is the first one).
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
set "TA_SHELL=powershell"
where pwsh >nul 2>nul && set "TA_SHELL=pwsh"
%TA_SHELL% -NoProfile -ExecutionPolicy Bypass -File "%~dp0talk-autopsy.ps1" %*
set "TA_CODE=%errorlevel%"
endlocal & exit /b %TA_CODE%
