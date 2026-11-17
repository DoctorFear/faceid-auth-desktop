$ErrorActionPreference = "Stop"
$devkitBin = Join-Path $env:LOCALAPPDATA "Programs\w64devkit\bin"
$env:Path = "$devkitBin;$env:Path"

Set-Location "C:\Users\ADMIN\faceid-auth-desktop\platforms\windows\credential_provider"

Write-Host "[*] Compiling resources.rc..." -ForegroundColor Cyan
& "$devkitBin\windres.exe" resources.rc -o resources.o

Write-Host "[*] Compiling SampleV2CredentialProvider.dll (64-bit)..." -ForegroundColor Cyan
& "$devkitBin\g++.exe" -shared -O2 -Wno-write-strings -finput-charset=UTF-8 -fexec-charset=UTF-8 `
    -DUNICODE -D_UNICODE -DWINVER=0x0603 -D_WIN32_WINNT=0x0603 `
    -D__in= -D__out= -D__deref_out= -D__override= `
    -DCRED_PACK_PROTECTED_CREDENTIALS=0x1 -DCRED_PACK_WOW_BUFFER=0x2 -DCRED_PACK_ID_PROVIDER_CREDENTIALS=0x4 `
    CSampleCredential.cpp CSampleProvider.cpp Dll.cpp guid.cpp helpers.cpp resources.o `
    samplev2credentialprovider.def `
    -o SampleV2CredentialProvider.dll `
    -lws2_32 -lshlwapi -lcredui -lsecur32 -lole32 -luuid -lgdi32 -lpropsys -static-libgcc -static-libstdc++

$item = Get-Item "SampleV2CredentialProvider.dll"
Write-Host "[+] Build SUCCESS: $($item.FullName) ($($item.Length) bytes, $($item.LastWriteTime))" -ForegroundColor Green
