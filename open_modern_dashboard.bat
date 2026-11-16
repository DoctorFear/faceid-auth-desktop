@echo off
title FaceID Hybrid - Modern Dashboard
cd /d "%~dp0"
if exist venv\Scripts\pythonw.exe (
    start "" venv\Scripts\pythonw.exe gui\modern_gui.py
) else if exist venv\Scripts\python.exe (
    start "" venv\Scripts\python.exe gui\modern_gui.py
) else (
    start "" python gui\modern_gui.py
)
exit
