@echo off
REM ==============================================================================
REM run_pam_simulation.bat - Kich ban kiem thu toan trinh PAM + Fallback tren Windows
REM ==============================================================================
chcp 65001 >nul
cd /d "%~dp0.."

echo ==============================================================================
echo   FACEID HYBRID - MO PHONG XAC THUC LINUX PAM VA CO CHE FALLBACK
echo   (Chay tren Windows Dev Environment)
echo ==============================================================================
echo.
echo [1] Kiem tra trang thai he thong...
.\venv\Scripts\python.exe scripts\faceidctl status
echo.

:MENU
echo ------------------------------------------------------------------------------
echo Chon che do kiem thu:
echo   [1] Kiem thu xac thuc 'sudo' (Ngung 0.75 + Bat buoc Liveness Chop mat/Cuoi)
echo   [2] Kiem thu xac thuc 'screensaver' (Ngung 0.65 + Mo sieu toc khong liveness)
echo   [3] Kiem thu Fallback khi Camera ban / Che camera / Khong dung mat (Exit 1)
echo   [4] Kiem thu Bypass khi Tat FaceID (faceidctl off - Fallback 100%%)
echo   [5] Kiem thu Phong thu Fuzz Testing Socket (6 bai test do ben)
echo   [6] Thoat
echo ------------------------------------------------------------------------------
set /p opt="Nhap lua chon [1-6]: "

if "%opt%"=="1" goto TEST_SUDO
if "%opt%"=="2" goto TEST_SCREEN
if "%opt%"=="3" goto TEST_FALLBACK
if "%opt%"=="4" goto TEST_BYPASS
if "%opt%"=="5" goto TEST_FUZZ
if "%opt%"=="6" goto EXIT_SCRIPT
goto MENU

:TEST_SUDO
echo.
echo [*] Dang khoi dong Daemon ngam trong 3 giay...
start /b "" .\venv\Scripts\python.exe daemon.py >nul 2>&1
timeout /t 2 /nobreak >nul
echo [*] Goi yeu cau xac thuc cho dich vu 'sudo' (User: son)...
.\venv\Scripts\python.exe scripts\faceid_pam_client.py son sudo
echo [Exit Code]: %errorlevel%
if %errorlevel% equ 0 (
    echo [+] KET QUA: PAM_SUCCESS (Cho phep lenh sudo thuc thi^)
) else (
    echo [-] KET QUA: FALLBACK PASSWORD (Chuyen sang nhap mat khau^)
)
taskkill /f /im python.exe >nul 2>&1
echo.
pause
goto MENU

:TEST_SCREEN
echo.
echo [*] Dang khoi dong Daemon ngam...
start /b "" .\venv\Scripts\python.exe daemon.py >nul 2>&1
timeout /t 2 /nobreak >nul
echo [*] Goi yeu cau xac thuc cho dich vu 'gnome-screensaver' (User: son)...
.\venv\Scripts\python.exe scripts\faceid_pam_client.py son gnome-screensaver
echo [Exit Code]: %errorlevel%
if %errorlevel% equ 0 (
    echo [+] KET QUA: PAM_SUCCESS (Mo khoa man hinh thanh cong^)
) else (
    echo [-] KET QUA: FALLBACK PASSWORD (Chuyen sang nhap mat khau^)
)
taskkill /f /im python.exe >nul 2>&1
echo.
pause
goto MENU

:TEST_FALLBACK
echo.
echo [*] Dang khoi dong Daemon ngam...
start /b "" .\venv\Scripts\python.exe daemon.py >nul 2>&1
timeout /t 2 /nobreak >nul
echo [CHU Y] Vui long CHE TAY LEN CAMERA hoac KHONG NHIN VAO WEBCAM de test Fallback!
echo [*] Dang kiem thu...
.\venv\Scripts\python.exe scripts\faceid_pam_client.py son sudo
echo [Exit Code]: %errorlevel%
if %errorlevel% equ 1 (
    echo [+] CHUNG MINH THANH CONG: Khi loi hoac khong thay mat, Exit Code luon la 1.
    echo     He dieu hanh Linux PAM se bat ngay: [sudo] password for son: (KHONG TREO MAY^)
)
taskkill /f /im python.exe >nul 2>&1
echo.
pause
goto MENU

:TEST_BYPASS
echo.
echo [*] Buoc 1: Tat cong tac FaceID...
.\venv\Scripts\python.exe scripts\faceidctl off
echo [*] Buoc 2: Chay client kiem thu...
.\venv\Scripts\python.exe scripts\faceid_pam_client.py son sudo
echo [Exit Code]: %errorlevel%
echo [*] Buoc 3: Bat lai cong tac FaceID ve ban dau...
.\venv\Scripts\python.exe scripts\faceidctl on
echo [+] He thong da khoi phuc trang thai san sang.
echo.
pause
goto MENU

:TEST_FUZZ
echo.
echo [*] Dang khoi dong Daemon ngam...
start /b "" .\venv\Scripts\python.exe daemon.py >nul 2>&1
timeout /t 2 /nobreak >nul
echo [*] Bat dau Fuzz Testing...
.\venv\Scripts\python.exe eval\fuzz_socket.py
taskkill /f /im python.exe >nul 2>&1
echo.
pause
goto MENU

:EXIT_SCRIPT
echo.
echo [*] Da don dep tien trinh. Hen gap lai!
