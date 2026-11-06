@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================================
echo   BAT FACEID DAEMON & AUTO-START KHI KHOI DONG MAY
echo ====================================================================
call "%~dp0venv\Scripts\python.exe" "%~dp0scripts\faceidctl" enable-autostart
call "%~dp0venv\Scripts\python.exe" "%~dp0scripts\faceidctl" status
pause
