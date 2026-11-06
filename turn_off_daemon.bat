@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================================
echo   TAT FACEID DAEMON & GO AUTO-START (FALLBACK PASSWORD 100%)
echo ====================================================================
call "%~dp0venv\Scripts\python.exe" "%~dp0scripts\faceidctl" disable-autostart
call "%~dp0venv\Scripts\python.exe" "%~dp0scripts\faceidctl" off
call "%~dp0venv\Scripts\python.exe" "%~dp0scripts\faceidctl" stop
call "%~dp0venv\Scripts\python.exe" "%~dp0scripts\faceidctl" status
pause
