$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# Duong dan project goc: di len 3 cap (credential_provider -> windows -> platforms -> faceid-auth-desktop)
$projectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $here))

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

Write-Host "`n[+] HOAN TAT CAI DAT WINDOWS CREDENTIAL PROVIDER!" -ForegroundColor Green
Start-Sleep -Seconds 1
