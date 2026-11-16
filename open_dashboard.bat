@echo off
chcp 65001 >nul
title FaceID Hybrid - Control Panel

cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0gui\app_gui.py"
exit
