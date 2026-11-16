@echo off
chcp 65001 >nul
:: Tu dong yeu cau quyen Administrator (UAC) neu chua co
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [*] Dang yeu cau quyen Administrator de go cai dat Credential Provider...
    powershell -NoProfile -Command "(New-Object -ComObject Shell.Application).ShellExecute('%~f0', '', '', 'runas', 1)"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\ADMIN\faceid-auth-desktop\platforms\windows\credential_provider\unregister_win_credential_provider.ps1"
schtasks /Delete /TN "FaceIDHybridDaemon" /F >nul 2>&1
schtasks /Delete /TN "FaceID_Hybrid_Daemon" /F >nul 2>&1
reg delete "HKLM\SOFTWARE\FaceIDHybrid" /f >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9876" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
