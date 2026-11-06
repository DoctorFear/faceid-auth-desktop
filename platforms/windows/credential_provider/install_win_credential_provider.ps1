$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $here)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CAI DAT WINDOWS CREDENTIAL PROVIDER & AUTO-START DAEMON " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Dat mat khau tai khoan Windows hien tai thanh 123456
$currentUser = $env:USERNAME
Write-Host "[1/4] Dang thiet lap mat khau '123456' cho user '$currentUser'..." -ForegroundColor Yellow
net user $currentUser 123456

# 2. Copy SampleV2CredentialProvider.dll vao C:\Windows\System32
$dllSrc = Join-Path $here "SampleV2CredentialProvider.dll"
$dllDst = "C:\Windows\System32\SampleV2CredentialProvider.dll"
Write-Host "[2/4] Dang copy SampleV2CredentialProvider.dll vao C:\Windows\System32..." -ForegroundColor Yellow
Copy-Item -Path $dllSrc -Destination $dllDst -Force

# 3. Dang ky Registry cho Credential Provider
$regFile = Join-Path $here "register.reg"
Write-Host "[3/4] Dang dang ky Registry (register.reg) voi Windows LogonUI..." -ForegroundColor Yellow
reg import $regFile

# 4. Dang ky Windows Task Scheduler ONSTART + Startup Registry de Daemon tu chay ngam khi bat may
Write-Host "[4/4] Dang dang ky Auto-Start cho FaceID Daemon khi khoi dong Windows (ONSTART & ONLOGON)..." -ForegroundColor Yellow
$pythonwExe = Join-Path $projectRoot "venv\Scripts\pythonw.exe"
$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"
$daemonScript = Join-Path $projectRoot "daemon.py"
$ctlScript = Join-Path $projectRoot "scripts\faceidctl"

# Dang ky Task Scheduler chay ngay khi Windows Boot (truoc ca man hinh Logon dau tien)
$action = New-ScheduledTaskAction -Execute $pythonwExe -Argument "`"$daemonScript`"" -WorkingDirectory $projectRoot
$triggerBoot = New-ScheduledTaskTrigger -AtStartup
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "FaceID_Hybrid_Daemon" -Action $action -Trigger @($triggerBoot, $triggerLogon) -User "SYSTEM" -RunLevel Highest -Settings $settings -Force | Out-Null

# Dang ky them vao HKCU Run va khoi dong Daemon ngay lap tuc
& $pythonExe $ctlScript enable-autostart

Write-Host "`n[+] HOAN TAT CAI DAT & AUTO-START THANH CONG!" -ForegroundColor Green
Write-Host "    • FaceID Daemon se TU DONG CHAY NGAM ngay khi ban bat nguon Windows (kể cả sau khi Shut down)." -ForegroundColor Green
Write-Host "    • Man hinh Win + L: Mo khoa sieu toc (Passive Liveness)" -ForegroundColor Green
Write-Host "    • Man hinh Bat may (Logon): Mo khoa kem thu thach Active Liveness" -ForegroundColor Green
Start-Sleep -Seconds 2
