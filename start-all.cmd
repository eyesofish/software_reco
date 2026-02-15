@echo off
setlocal
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0start-all.ps1" %*
endlocal
