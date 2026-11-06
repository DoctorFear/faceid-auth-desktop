$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $here)

Write-Host "[*] Dang dung FaceID Daemon va go bo Auto-Start..." -ForegroundColor Yellow
$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"
$ctlScript = Join-Path $projectRoot "scripts\faceidctl"
if (Test-Path $pythonExe) {
    & $pythonExe $ctlScript disable-autostart
    & $pythonExe $ctlScript stop
}
schtasks /Delete /TN "FaceID_Hybrid_Daemon" /F 2>$null | Out-Null

Write-Host "[*] Dang go dang ky SampleV2CredentialProvider khoi Windows LogonUI..." -ForegroundColor Yellow
$unregFile = Join-Path $here "Unregister.reg"
reg import $unregFile

$dllDst = "C:\Windows\System32\SampleV2CredentialProvider.dll"
if (Test-Path $dllDst) {
    Remove-Item $dllDst -Force -ErrorAction SilentlyContinue
}
Write-Host "[+] Da go dang ky Credential Provider va Auto-Start thanh cong!" -ForegroundColor Green
Start-Sleep -Seconds 2
