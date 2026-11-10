@echo off
:: Double-click launcher for the Dz8 lab (Windows).
:: Runs run.ps1 from the same folder and keeps the window open so the printed
:: UI URL and any error stay visible. Arguments are passed through.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "code=%ERRORLEVEL%"
echo.
pause
exit /b %code%
