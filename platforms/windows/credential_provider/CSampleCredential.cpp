//
// FaceID Hybrid Credential Provider - Implementation of CSampleCredential
// Connects to FaceID Python Daemon over TCP 127.0.0.1:9876 on Win + L (LogonUI.exe)
// Fallback password: 123456
//

#ifndef WIN32_NO_STATUS
#include <ntstatus.h>
#define WIN32_NO_STATUS
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <unknwn.h>
#include <stdio.h>
#include <string.h>
#include "CSampleCredential.h"
#include "CSampleProvider.h"
#include "guid.h"

#define FACEID_DEFAULT_PASSWORD L"123456"
#define FACEID_DAEMON_HOST       "127.0.0.1"
#define FACEID_DAEMON_PORT       9876
#define FACEID_POLICY_PATH       L"C:\\Users\\ADMIN\\faceid-auth-desktop\\config\\policy.json"

// Xac dinh chinh xac service thuc te ("windows-unlock" cho Win + L, "windows-logon" cho luc moi bat may)
// Luu y: Tren Windows 10/11, khi bam Win + L, LogonUI thuong truyen CPUS_LOGON thay vi CPUS_UNLOCK_WORKSTATION.
// Do do can kiem tra xem session hien tai da co user dang nhap san chua (WTSUserName != empty).
static const char* ResolveEffectiveServiceName(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus)
{
    if (cpus != CPUS_LOGON)
    {
        return "windows-unlock";
    }

    HMODULE hWts = LoadLibraryW(L"wtsapi32.dll");
    if (hWts)
    {
        typedef BOOL(WINAPI *PFN_WTSQuerySessionInformationW)(HANDLE, DWORD, DWORD, LPWSTR*, DWORD*);
        typedef VOID(WINAPI *PFN_WTSFreeMemory)(PVOID);

        PFN_WTSQuerySessionInformationW pQuery = (PFN_WTSQuerySessionInformationW)GetProcAddress(hWts, "WTSQuerySessionInformationW");
        PFN_WTSFreeMemory pFree = (PFN_WTSFreeMemory)GetProcAddress(hWts, "WTSFreeMemory");
        if (pQuery && pFree)
        {
            LPWSTR pszUser = NULL;
            DWORD dwBytes = 0;
            // WTS_CURRENT_SERVER_HANDLE = NULL, WTS_CURRENT_SESSION = (DWORD)-1, WTSUserName = 5
            if (pQuery(NULL, (DWORD)-1, 5, &pszUser, &dwBytes) && pszUser != NULL)
            {
                bool hasLoggedUser = (pszUser[0] != L'\0');
                pFree(pszUser);
                FreeLibrary(hWts);
                if (hasLoggedUser)
                {
                    return "windows-unlock";
                }
                return "windows-logon";
            }
            if (pszUser) pFree(pszUser);
        }
        FreeLibrary(hWts);
    }

    return "windows-logon";
}

// Doc mat khau du phong dong tu Registry HKLM\SOFTWARE\FaceIDHybrid (hoac dung mac dinh 123456)
static void GetDynamicFallbackPassword(wchar_t* pwzOut, size_t cchOut)
{
    if (!pwzOut || cchOut == 0) return;
    StringCchCopyW(pwzOut, cchOut, FACEID_DEFAULT_PASSWORD);

    HKEY hKey;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\FaceIDHybrid", 0, KEY_READ, &hKey) == ERROR_SUCCESS)
    {
        wchar_t szBuf[128] = {0};
        DWORD cbData = sizeof(szBuf);
        DWORD dwType = REG_SZ;
        if (RegQueryValueExW(hKey, L"FallbackPassword", NULL, &dwType, (LPBYTE)szBuf, &cbData) == ERROR_SUCCESS)
        {
            if (szBuf[0] != L'\0')
            {
                StringCchCopyW(pwzOut, cchOut, szBuf);
            }
        }
        RegCloseKey(hKey);
    }
}

// Trich xuat gia tri chuoi cua mot key trong dong JSON (vi du: "message": "...")
// Ho tro ca UTF-8 thuan va JSON escape (\uXXXX)
static bool ExtractJsonStringField(const char* szJsonLine, const char* szKey, wchar_t* pwzOut, size_t cchOut)
{
    if (!szJsonLine || !szKey || !pwzOut || cchOut == 0) return false;
    pwzOut[0] = L'\0';

    char szPattern[64];
    snprintf(szPattern, sizeof(szPattern), "\"%s\"", szKey);
    const char* pKey = strstr(szJsonLine, szPattern);
    if (!pKey) return false;

    const char* pColon = strchr(pKey + strlen(szPattern), ':');
    if (!pColon) return false;

    const char* pQuoteStart = strchr(pColon + 1, '"');
    if (!pQuoteStart) return false;
    pQuoteStart++;

    // Duyet va giai ma escape: neu gap \uXXXX thi parse truc tiep thanh wchar_t
    // neu gap UTF-8 multibyte thi chuyen doi qua MultiByteToWideChar
    size_t outIdx = 0;
    const char* p = pQuoteStart;
    char utf8Buf[8];
    int utf8Len = 0;

    while (*p != '\0' && *p != '"' && outIdx < cchOut - 1)
    {
        if (*p == '\\' && *(p + 1) == 'u' && *(p + 2) && *(p + 3) && *(p + 4) && *(p + 5))
        {
            // Parse 4 hex digits \uXXXX
            char hexBuf[5] = { *(p + 2), *(p + 3), *(p + 4), *(p + 5), '\0' };
            wchar_t uChar = (wchar_t)strtol(hexBuf, NULL, 16);
            pwzOut[outIdx++] = uChar;
            p += 6;
            continue;
        }
        else if (*p == '\\' && *(p + 1) != '\0')
        {
            p++;
        }

        // Xu ly UTF-8 byte
        unsigned char uc = (unsigned char)*p;
        if (uc < 0x80)
        {
            pwzOut[outIdx++] = (wchar_t)uc;
            p++;
        }
        else
        {
            // Tinh so byte UTF-8
            int nBytes = 1;
            if ((uc & 0xE0) == 0xC0) nBytes = 2;
            else if ((uc & 0xF0) == 0xE0) nBytes = 3;
            else if ((uc & 0xF8) == 0xF0) nBytes = 4;

            char mb[8] = {0};
            for (int b = 0; b < nBytes && *p != '\0' && *p != '"'; b++)
            {
                mb[b] = *p++;
            }
            wchar_t wTemp[4] = {0};
            int wLen = MultiByteToWideChar(CP_UTF8, 0, mb, -1, wTemp, 4);
            for (int wi = 0; wi < wLen && wTemp[wi] != L'\0' && outIdx < cchOut - 1; wi++)
            {
                pwzOut[outIdx++] = wTemp[wi];
            }
        }
    }
    pwzOut[outIdx] = L'\0';
    return (outIdx > 0);
}

// Kiem tra nhanh xem FaceID Daemon co dang chay tren 127.0.0.1:9876 hay khong (timeout 150ms)
static bool IsFaceIDDaemonAliveQuick()
{
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return false;
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) { WSACleanup(); return false; }

    u_long mode = 1;
    ioctlsocket(s, FIONBIO, &mode);

    sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(FACEID_DAEMON_PORT);
    addr.sin_addr.s_addr = inet_addr(FACEID_DAEMON_HOST);

    int rc = connect(s, (sockaddr*)&addr, sizeof(addr));
    bool alive = false;
    if (rc == 0)
    {
        alive = true;
    }
    else if (WSAGetLastError() == WSAEWOULDBLOCK)
    {
        fd_set wfds;
        FD_ZERO(&wfds);
        FD_SET(s, &wfds);
        timeval tv = { 0, 150000 }; // 150ms
        if (select(0, NULL, &wfds, NULL, &tv) > 0)
        {
            int err = 0; int len = sizeof(err);
            if (getsockopt(s, SOL_SOCKET, SO_ERROR, (char*)&err, &len) == 0 && err == 0)
                alive = true;
        }
    }
    closesocket(s);
    WSACleanup();
    return alive;
}

// Ket noi TCP Socket toi FaceID Daemon (127.0.0.1:9876) de yeu cau quet khuon mat
bool QueryFaceIDDaemon(CSampleCredential *pCredential, PCWSTR pwzQualifiedUser, CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, PWSTR pwzStatusOut, size_t cchStatusOut, HANDLE hStopEvent)
{
    if (pwzStatusOut && cchStatusOut > 0)
    {
        StringCchCopyW(pwzStatusOut, cchStatusOut, L"Dang ket noi FaceID Daemon...");
    }

    // Tach ten user ngan gon (vi du: DESKTOP\ADMIN -> ADMIN)
    PCWSTR pwzShortUser = L"ADMIN";
    if (pwzQualifiedUser && pwzQualifiedUser[0] != L'\0')
    {
        PCWSTR pSlash = wcsrchr(pwzQualifiedUser, L'\\');
        pwzShortUser = pSlash ? (pSlash + 1) : pwzQualifiedUser;
    }

    char szUserUtf8[128] = {0};
    WideCharToMultiByte(CP_UTF8, 0, pwzShortUser, -1, szUserUtf8, sizeof(szUserUtf8) - 1, NULL, NULL);
    if (szUserUtf8[0] == '\0')
    {
        strcpy(szUserUtf8, "ADMIN");
    }

    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        if (pwzStatusOut) StringCchCopyW(pwzStatusOut, cchStatusOut, L"Loi khoi tao Winsock -> Vui long nhap mat khau");
        return false;
    }

    SOCKET sock = INVALID_SOCKET;

    // Co che ket noi thong minh (Smart Connect with Retry):
    // Khi vua bat may (CPUS_LOGON), Daemon can khoang 2-3s de nap model AI vao RAM tren Windows boot.
    // Thu ket noi toi da 30 lan (30 x 450ms = ~13.5 giay) de hoan toan khong bi bao loi "Daemon tat".
    int maxAttempts = (cpus == CPUS_LOGON) ? 30 : 12;
    bool connected = false;

    sockaddr_in addr;
    ZeroMemory(&addr, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(FACEID_DAEMON_PORT);
    addr.sin_addr.s_addr = inet_addr(FACEID_DAEMON_HOST);

    for (int attempt = 0; attempt < maxAttempts; ++attempt)
    {
        if (hStopEvent && WaitForSingleObject(hStopEvent, 0) == WAIT_OBJECT_0)
        {
            break;
        }

        sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (sock == INVALID_SOCKET)
        {
            break;
        }

        u_long nonBlocking = 1;
        ioctlsocket(sock, FIONBIO, &nonBlocking);
        connect(sock, (sockaddr*)&addr, sizeof(addr));

        fd_set writeSet;
        FD_ZERO(&writeSet);
        FD_SET(sock, &writeSet);
        timeval tvConn;
        tvConn.tv_sec = 0;
        tvConn.tv_usec = 350000; // 350ms moi lan thu

        int sel = select(0, NULL, &writeSet, NULL, &tvConn);
        if (sel > 0)
        {
            connected = true;
            break;
        }

        closesocket(sock);
        sock = INVALID_SOCKET;

        // Cho 100ms giua cac lan retry (co kiem tra hStopEvent)
        if (hStopEvent)
        {
            if (WaitForSingleObject(hStopEvent, 100) == WAIT_OBJECT_0)
            {
                break;
            }
        }
        else
        {
            Sleep(100);
        }
    }

    if (!connected)
    {
        if (sock != INVALID_SOCKET)
        {
            closesocket(sock);
        }
        WSACleanup();
        if (pwzStatusOut)
        {
            StringCchCopyW(pwzStatusOut, cchStatusOut, L"M\u1EDDi b\u1EA1n nh\u1EADp m\u1EADt kh\u1EA9u");
        }
        return false;
    }

    // Da ket noi thanh cong toi Daemon!
    if (pCredential)
    {
        pCredential->UpdateStatusText(L"Đang nhận diện khuôn mặt... Vui lòng nhìn thẳng vào camera");
    }

    // Chuyen ve blocking mode voi timeout doc 16.0 giay (ho tro timeout cau hinh len toi 15s + Active Liveness)
    u_long blockMode = 0;
    ioctlsocket(sock, FIONBIO, &blockMode);
    DWORD recvTimeoutMs = 16000;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&recvTimeoutMs, sizeof(recvTimeoutMs));
    DWORD sendTimeoutMs = 2000;
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&sendTimeoutMs, sizeof(sendTimeoutMs));

    const char* szService = ResolveEffectiveServiceName(cpus);
    char szReq[256];
    snprintf(szReq, sizeof(szReq), "{\"action\": \"auth\", \"user\": \"%s\", \"service\": \"%s\"}\n", szUserUtf8, szService);
    if (send(sock, szReq, (int)strlen(szReq), 0) == SOCKET_ERROR)
    {
        closesocket(sock);
        WSACleanup();
        if (pwzStatusOut) StringCchCopyW(pwzStatusOut, cchStatusOut, L"Không thể kết nối FaceID. Vui lòng nhập mật khẩu");
        return false;
    }

    char recvBuf[4096];
    char lineBuf[8192];
    size_t lineLen = 0;
    bool authSuccess = false;
    bool done = false;

    while (!done)
    {
        if (hStopEvent && WaitForSingleObject(hStopEvent, 0) == WAIT_OBJECT_0)
        {
            break;
        }

        int n = recv(sock, recvBuf, sizeof(recvBuf) - 1, 0);
        if (n <= 0)
        {
            break;
        }

        for (int i = 0; i < n; ++i)
        {
            char c = recvBuf[i];
            if (c == '\n' || lineLen >= sizeof(lineBuf) - 1)
            {
                lineBuf[lineLen] = '\0';
                if (lineLen > 0)
                {
                    if (strstr(lineBuf, "\"event\": \"progress\"") || strstr(lineBuf, "\"event\":\"progress\""))
                    {
                        wchar_t wMsg[256] = {0};
                        if (ExtractJsonStringField(lineBuf, "message", wMsg, ARRAYSIZE(wMsg)) && pCredential)
                        {
                            pCredential->UpdateStatusText(wMsg);
                        }
                    }
                    else if (strstr(lineBuf, "\"status\": \"SUCCESS\"") || strstr(lineBuf, "\"status\":\"SUCCESS\""))
                    {
                        authSuccess = true;
                        wchar_t wUser[128] = {0};
                        if (ExtractJsonStringField(lineBuf, "matched_user", wUser, ARRAYSIZE(wUser)) && wUser[0] != L'\0')
                        {
                            if (pwzStatusOut)
                            {
                                StringCchPrintfW(pwzStatusOut, cchStatusOut, L"Xác thực thành công (%s)! Đang mở khóa...", wUser);
                            }
                        }
                        else
                        {
                            if (pwzStatusOut)
                            {
                                StringCchCopyW(pwzStatusOut, cchStatusOut, L"Xác thực thành công! Đang mở khóa máy tính...");
                            }
                        }
                        done = true;
                        break;
                    }
                    else if (strstr(lineBuf, "\"status\": \"LOCKED_OUT\"") || strstr(lineBuf, "\"status\":\"LOCKED_OUT\""))
                    {
                        if (pwzStatusOut)
                        {
                            StringCchCopyW(pwzStatusOut, cchStatusOut, L"FaceID đã tạm khóa do thử sai nhiều lần. Vui lòng nhập mật khẩu");
                        }
                        done = true;
                        break;
                    }
                    else if (strstr(lineBuf, "\"status\": \"CAMERA_BUSY\"") || strstr(lineBuf, "\"status\":\"CAMERA_BUSY\""))
                    {
                        if (pwzStatusOut)
                        {
                            StringCchCopyW(pwzStatusOut, cchStatusOut, L"Camera đang bận. Vui lòng sử dụng mật mã dự phòng");
                        }
                        done = true;
                        break;
                    }
                    else if (strstr(lineBuf, "\"status\": \"BYPASS\"") || strstr(lineBuf, "\"status\":\"BYPASS\""))
                    {
                        if (pwzStatusOut)
                        {
                            StringCchCopyW(pwzStatusOut, cchStatusOut, L"FaceID đang tạm tắt. Vui lòng nhập mật khẩu mở máy");
                        }
                        done = true;
                        break;
                    }
                    else if (strstr(lineBuf, "\"status\":"))
                    {
                        if (pwzStatusOut)
                        {
                            StringCchCopyW(pwzStatusOut, cchStatusOut, L"Không nhận diện được khuôn mặt. Vui lòng nhập mật mã");
                        }
                        done = true;
                        break;
                    }
                }
                lineLen = 0;
            }
            else if (c != '\r')
            {
                lineBuf[lineLen++] = c;
            }
        }
    }

    closesocket(sock);
    WSACleanup();
    return authSuccess;
}

// Ham phu ban phim ao Enter vao LogonUI tren thread doc lap
static DWORD WINAPI TriggerLogonSubmitThread(LPVOID)
{
    for (int i = 0; i < 3; ++i)
    {
        Sleep(120);
        keybd_event(VK_RETURN, 0, 0, 0);
        Sleep(25);
        keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0);
    }
    return 0;
}

static void AutoSubmitLogon()
{
    HANDLE hThread = CreateThread(NULL, 0, TriggerLogonSubmitThread, NULL, 0, NULL);
    if (hThread)
    {
        CloseHandle(hThread);
    }
}

// Thread worker quet khuon mat ngam de khong lam treo giao dien LogonUI
DWORD WINAPI FaceIDWorkerThread(LPVOID lpParam)
{
    CSampleCredential *pThis = static_cast<CSampleCredential*>(lpParam);
    if (!pThis) return 0;

    // Gan worker thread vao Input Desktop hien tai (Winlogon secure desktop) de
    // GetAsyncKeyState doc duoc phim/chuot. Phai goi TRUOC moi lenh tao cua so/COM.
    HDESK hInputDesk = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS | DESKTOP_HOOKCONTROL | GENERIC_READ);
    if (hInputDesk)
    {
        SetThreadDesktop(hInputDesk);
    }

    HRESULT hrCom = CoInitializeEx(NULL, COINIT_MULTITHREADED);

    // Che do Tu Dong Quet Ngay: Man hinh 1 (phong canh/dong ho) chi bien mat khi nguoi dung
    // BAM PHIM, CLICK CHUOT hoac DI CHUYEN CHUOT/VUOT MAN HINH.
    // Nguoi dung chi can bat ky thao tac nao de chuyen sang Man hinh 2 la quet ngay lap tuc!
    wchar_t szStatus[256] = {0};
    bool bSuccess = QueryFaceIDDaemon(pThis, pThis->_pszQualifiedUserName, pThis->_cpus, szStatus, ARRAYSIZE(szStatus), pThis->_hStopEvent);

    if (pThis->IsStopRequested())
    {
        pThis->_fWorkerRunning = FALSE;
        if (SUCCEEDED(hrCom)) CoUninitialize();
        pThis->Release();
        return 0;
    }

    if (bSuccess)
    {
        pThis->OnFaceIDSuccess(szStatus);
    }
    else
    {
        if (szStatus[0] != L'\0')
        {
            pThis->UpdateStatusText(szStatus);
        }
    }

    pThis->_fWorkerRunning = FALSE;
    if (SUCCEEDED(hrCom)) CoUninitialize();
    if (hInputDesk) CloseDesktop(hInputDesk);
    pThis->Release();
    return 0;
}

void CSampleCredential::StartWorkerThread()
{
    EnterCriticalSection(&_csLock);
    if (_fWorkerRunning && _hWorkerThread != NULL)
    {
        LeaveCriticalSection(&_csLock);
        return;
    }
    if (_hStopEvent)
    {
        ResetEvent(_hStopEvent);
    }
    _fWorkerRunning = TRUE;
    this->AddRef();

    _hWorkerThread = CreateThread(NULL, 0, FaceIDWorkerThread, this, 0, NULL);
    if (!_hWorkerThread)
    {
        _fWorkerRunning = FALSE;
        this->Release();
    }
    LeaveCriticalSection(&_csLock);
}

void CSampleCredential::StopWorkerThread()
{
    HANDLE hThreadToWait = NULL;
    EnterCriticalSection(&_csLock);
    if (_hStopEvent)
    {
        SetEvent(_hStopEvent);
    }
    if (_hWorkerThread)
    {
        hThreadToWait = _hWorkerThread;
        _hWorkerThread = NULL;
    }
    LeaveCriticalSection(&_csLock);

    if (hThreadToWait)
    {
        WaitForSingleObject(hThreadToWait, 1000);
        CloseHandle(hThreadToWait);
    }
    _fWorkerRunning = FALSE;
}

bool CSampleCredential::IsStopRequested()
{
    if (!_hStopEvent) return false;
    return (WaitForSingleObject(_hStopEvent, 0) == WAIT_OBJECT_0);
}

void CSampleCredential::UpdateStatusText(PCWSTR pszText)
{
    EnterCriticalSection(&_csLock);
    if (_rgFieldStrings[SFI_FULLNAME_TEXT] && pszText && wcscmp(_rgFieldStrings[SFI_FULLNAME_TEXT], pszText) == 0)
    {
        LeaveCriticalSection(&_csLock);
        return;
    }
    CoTaskMemFree(_rgFieldStrings[SFI_FULLNAME_TEXT]);
    SHStrDupW(pszText, &_rgFieldStrings[SFI_FULLNAME_TEXT]);
    LeaveCriticalSection(&_csLock);

    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->BeginFieldUpdates();
        _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, pszText);
        _pCredProvCredentialEvents->EndFieldUpdates();
    }
}

void CSampleCredential::OnFaceIDSuccess(PCWSTR pszSuccessStatus)
{
    wchar_t szPw[128] = {0};
    GetDynamicFallbackPassword(szPw, ARRAYSIZE(szPw));

    EnterCriticalSection(&_csLock);
    CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
    SHStrDupW(szPw, &_rgFieldStrings[SFI_PASSWORD]);

    PCWSTR pszFinalMsg = (pszSuccessStatus && pszSuccessStatus[0] != L'\0') 
        ? pszSuccessStatus 
        : L"Xác thực thành công! Đang tự động mở khóa...";

    CoTaskMemFree(_rgFieldStrings[SFI_FULLNAME_TEXT]);
    SHStrDupW(pszFinalMsg, &_rgFieldStrings[SFI_FULLNAME_TEXT]);
    LeaveCriticalSection(&_csLock);

    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->BeginFieldUpdates();
        _pCredProvCredentialEvents->SetFieldString(this, SFI_PASSWORD, _rgFieldStrings[SFI_PASSWORD]);
        _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, _rgFieldStrings[SFI_FULLNAME_TEXT]);
        _pCredProvCredentialEvents->EndFieldUpdates();
    }

    // Nghi 400ms de nguoi dung nhin ro chu thong bao thanh cong truoc khi chuyen man hinh vao Desktop
    Sleep(400);

    // 1. Kich hoat Native Windows Auto-Logon thong qua ICredentialProviderEvents::CredentialsChanged
    if (_pProvider)
    {
        _pProvider->TriggerAutoLogon();
    }

    // 2. Gui phim ao Enter du phong
    AutoSubmitLogon();
}

CSampleCredential::CSampleCredential():
    _cRef(1),
    _pCredProvCredentialEvents(nullptr),
    _pszUserSid(nullptr),
    _pszQualifiedUserName(nullptr),
    _fIsLocalUser(false),
    _fChecked(false),
    _fShowControls(false),
    _dwComboIndex(0),
    _hWorkerThread(NULL),
    _hStopEvent(NULL),
    _fWorkerRunning(FALSE),
    _pProvider(nullptr)
{
    DllAddRef();

    InitializeCriticalSection(&_csLock);
    _hStopEvent = CreateEvent(NULL, TRUE, FALSE, NULL);

    ZeroMemory(_rgCredProvFieldDescriptors, sizeof(_rgCredProvFieldDescriptors));
    ZeroMemory(_rgFieldStatePairs, sizeof(_rgFieldStatePairs));
    ZeroMemory(_rgFieldStrings, sizeof(_rgFieldStrings));
}

CSampleCredential::~CSampleCredential()
{
    StopWorkerThread();

    if (_hStopEvent)
    {
        CloseHandle(_hStopEvent);
        _hStopEvent = NULL;
    }
    DeleteCriticalSection(&_csLock);

    if (_rgFieldStrings[SFI_PASSWORD])
    {
        size_t lenPassword = wcslen(_rgFieldStrings[SFI_PASSWORD]);
        SecureZeroMemory(_rgFieldStrings[SFI_PASSWORD], lenPassword * sizeof(*_rgFieldStrings[SFI_PASSWORD]));
    }
    for (int i = 0; i < ARRAYSIZE(_rgFieldStrings); i++)
    {
        CoTaskMemFree(_rgFieldStrings[i]);
        CoTaskMemFree(_rgCredProvFieldDescriptors[i].pszLabel);
    }
    CoTaskMemFree(_pszUserSid);
    CoTaskMemFree(_pszQualifiedUserName);
    DllRelease();
}

HRESULT CSampleCredential::Initialize(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus,
                                      _In_ CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR const *rgcpfd,
                                      _In_ FIELD_STATE_PAIR const *rgfsp,
                                      _In_ ICredentialProviderUser *pcpUser)
{
    HRESULT hr = S_OK;
    _cpus = cpus;

    GUID guidProvider;
    pcpUser->GetProviderID(&guidProvider);
    _fIsLocalUser = (guidProvider == Identity_LocalUserProvider);

    for (DWORD i = 0; SUCCEEDED(hr) && i < ARRAYSIZE(_rgCredProvFieldDescriptors); i++)
    {
        _rgFieldStatePairs[i] = rgfsp[i];
        hr = FieldDescriptorCopy(rgcpfd[i], &_rgCredProvFieldDescriptors[i]);
    }

    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"VinFace Authentication", &_rgFieldStrings[SFI_LABEL]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"VinFace Authentication", &_rgFieldStrings[SFI_LARGE_TEXT]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"", &_rgFieldStrings[SFI_EDIT_TEXT]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"", &_rgFieldStrings[SFI_PASSWORD]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"Submit", &_rgFieldStrings[SFI_SUBMIT_BUTTON]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"Checkbox", &_rgFieldStrings[SFI_CHECKBOX]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"Combobox", &_rgFieldStrings[SFI_COMBOBOX]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"Quét lại khuôn mặt VinFace", &_rgFieldStrings[SFI_LAUNCHWINDOW_LINK]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"Hide additional controls", &_rgFieldStrings[SFI_HIDECONTROLS_LINK]);
    }
    if (SUCCEEDED(hr))
    {
        hr = pcpUser->GetStringValue(PKEY_Identity_QualifiedUserName, &_pszQualifiedUserName);
    }
    if (SUCCEEDED(hr))
    {
        wchar_t szString[256];
        StringCchCopyW(szString, ARRAYSIZE(szString), L"Nhìn thẳng vào camera để mở máy hoặc nhập mật khẩu");
        hr = SHStrDupW(szString, &_rgFieldStrings[SFI_FULLNAME_TEXT]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"", &_rgFieldStrings[SFI_DISPLAYNAME_TEXT]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"", &_rgFieldStrings[SFI_LOGONSTATUS_TEXT]);
    }
    if (SUCCEEDED(hr))
    {
        hr = pcpUser->GetSid(&_pszUserSid);
    }

    return hr;
}

HRESULT CSampleCredential::Advise(_In_ ICredentialProviderCredentialEvents *pcpce)
{
    if (_pCredProvCredentialEvents != nullptr)
    {
        _pCredProvCredentialEvents->Release();
    }
    return pcpce->QueryInterface(IID_PPV_ARGS(&_pCredProvCredentialEvents));
}

HRESULT CSampleCredential::UnAdvise()
{
    StopWorkerThread();
    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->Release();
    }
    _pCredProvCredentialEvents = nullptr;
    return S_OK;
}

// Khi tile duoc chon tren man hinh LogonUI
HRESULT CSampleCredential::SetSelected(_Out_ BOOL *pbAutoLogon)
{
    *pbAutoLogon = FALSE;

    // Neu mat khau da duoc dien (FaceID da xac thuc thanh cong truoc do) -> Set pbAutoLogon = TRUE ngay!
    if (_rgFieldStrings[SFI_PASSWORD] && _rgFieldStrings[SFI_PASSWORD][0] != L'\0')
    {
        *pbAutoLogon = TRUE;
        return S_OK;
    }

    bool isDaemonAlive = IsFaceIDDaemonAliveQuick();

    // Cap nhat trang thai hien/an cua nut CommandLink "Quet lai khuon mat VinFace"
    // Neu Daemon tat -> an nut di (CPFS_HIDDEN) de nguoi dung khong bam nham
    EnterCriticalSection(&_csLock);
    _rgFieldStatePairs[SFI_LAUNCHWINDOW_LINK].cpfs = isDaemonAlive ? CPFS_DISPLAY_IN_SELECTED_TILE : CPFS_HIDDEN;
    LeaveCriticalSection(&_csLock);

    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->BeginFieldUpdates();
        _pCredProvCredentialEvents->SetFieldState(this, SFI_LAUNCHWINDOW_LINK, _rgFieldStatePairs[SFI_LAUNCHWINDOW_LINK].cpfs);
        _pCredProvCredentialEvents->EndFieldUpdates();
    }

    // Neu dich vu Daemon dang tat -> Hien thong bao moi nhap mat khau
    if (!isDaemonAlive)
    {
        UpdateStatusText(L"Dịch vụ VinFace đang tắt. Vui lòng nhập mật khẩu mở máy");
        return S_OK;
    }

    // Che do Enter de quet: chi dung cho, khi nguoi dung bam Enter o GetSerialization moi quet
    UpdateStatusText(L"Nhìn vào camera để mở khóa và nhấn Enter");

    return S_OK;
}

HRESULT CSampleCredential::SetDeselected()
{
    HRESULT hr = S_OK;
    StopWorkerThread();
    _fChecked = FALSE;

    EnterCriticalSection(&_csLock);
    if (_rgFieldStrings[SFI_PASSWORD])
    {
        size_t lenPassword = wcslen(_rgFieldStrings[SFI_PASSWORD]);
        SecureZeroMemory(_rgFieldStrings[SFI_PASSWORD], lenPassword * sizeof(*_rgFieldStrings[SFI_PASSWORD]));

        CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
        hr = SHStrDupW(L"", &_rgFieldStrings[SFI_PASSWORD]);

        if (SUCCEEDED(hr) && _pCredProvCredentialEvents)
        {
            _pCredProvCredentialEvents->SetFieldString(this, SFI_PASSWORD, _rgFieldStrings[SFI_PASSWORD]);
        }
    }
    LeaveCriticalSection(&_csLock);

    return hr;
}

HRESULT CSampleCredential::GetFieldState(DWORD dwFieldID,
                                         _Out_ CREDENTIAL_PROVIDER_FIELD_STATE *pcpfs,
                                         _Out_ CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE *pcpfis)
{
    HRESULT hr;
    if ((dwFieldID < ARRAYSIZE(_rgFieldStatePairs)))
    {
        EnterCriticalSection(&_csLock);
        if (dwFieldID == SFI_LAUNCHWINDOW_LINK)
        {
            // Kiem tra truc tiep neu Daemon khong chay thi an nut ngay lap tuc
            if (!IsFaceIDDaemonAliveQuick())
            {
                *pcpfs = CPFS_HIDDEN;
                *pcpfis = CPFIS_NONE;
                LeaveCriticalSection(&_csLock);
                return S_OK;
            }
        }
        *pcpfs = _rgFieldStatePairs[dwFieldID].cpfs;
        *pcpfis = _rgFieldStatePairs[dwFieldID].cpfis;
        LeaveCriticalSection(&_csLock);
        hr = S_OK;
    }
    else
    {
        hr = E_INVALIDARG;
    }
    return hr;
}

HRESULT CSampleCredential::GetStringValue(DWORD dwFieldID, _Outptr_result_nullonfailure_ PWSTR *ppwsz)
{
    HRESULT hr;
    *ppwsz = nullptr;

    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors))
    {
        EnterCriticalSection(&_csLock);
        hr = SHStrDupW(_rgFieldStrings[dwFieldID], ppwsz);
        LeaveCriticalSection(&_csLock);
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::GetBitmapValue(DWORD dwFieldID, _Outptr_result_nullonfailure_ HBITMAP *phbmp)
{
    HRESULT hr;
    *phbmp = nullptr;

    if ((SFI_TILEIMAGE == dwFieldID))
    {
        HBITMAP hbmp = LoadBitmap(HINST_THISDLL, MAKEINTRESOURCE(IDB_TILE_IMAGE));
        if (hbmp != nullptr)
        {
            hr = S_OK;
            *phbmp = hbmp;
        }
        else
        {
            hr = HRESULT_FROM_WIN32(GetLastError());
        }
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::GetSubmitButtonValue(DWORD dwFieldID, _Out_ DWORD *pdwAdjacentTo)
{
    HRESULT hr;
    if (SFI_SUBMIT_BUTTON == dwFieldID)
    {
        *pdwAdjacentTo = SFI_PASSWORD;
        hr = S_OK;
    }
    else
    {
        hr = E_INVALIDARG;
    }
    return hr;
}

HRESULT CSampleCredential::SetStringValue(DWORD dwFieldID, _In_ PCWSTR pwz)
{
    HRESULT hr;
    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_EDIT_TEXT == _rgCredProvFieldDescriptors[dwFieldID].cpft ||
        CPFT_PASSWORD_TEXT == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        EnterCriticalSection(&_csLock);
        PWSTR *ppwszStored = &_rgFieldStrings[dwFieldID];
        CoTaskMemFree(*ppwszStored);
        hr = SHStrDupW(pwz, ppwszStored);
        LeaveCriticalSection(&_csLock);
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::GetCheckboxValue(DWORD dwFieldID, _Out_ BOOL *pbChecked, _Outptr_result_nullonfailure_ PWSTR *ppwszLabel)
{
    HRESULT hr;
    *ppwszLabel = nullptr;

    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_CHECKBOX == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        *pbChecked = _fChecked;
        hr = SHStrDupW(_rgFieldStrings[SFI_CHECKBOX], ppwszLabel);
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::SetCheckboxValue(DWORD dwFieldID, BOOL bChecked)
{
    HRESULT hr;
    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_CHECKBOX == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        _fChecked = bChecked;
        hr = S_OK;
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::GetComboBoxValueCount(DWORD dwFieldID, _Out_ DWORD *pcItems, _Deref_out_range_(<, *pcItems) _Out_ DWORD *pdwSelectedItem)
{
    HRESULT hr;
    *pcItems = 0;
    *pdwSelectedItem = 0;

    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_COMBOBOX == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        *pcItems = ARRAYSIZE(s_rgComboBoxStrings);
        *pdwSelectedItem = 0;
        hr = S_OK;
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::GetComboBoxValueAt(DWORD dwFieldID, DWORD dwItem, _Outptr_result_nullonfailure_ PWSTR *ppwszItem)
{
    HRESULT hr;
    *ppwszItem = nullptr;

    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_COMBOBOX == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        hr = SHStrDupW(s_rgComboBoxStrings[dwItem], ppwszItem);
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

HRESULT CSampleCredential::SetComboBoxSelectedValue(DWORD dwFieldID, DWORD dwSelectedItem)
{
    HRESULT hr;
    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_COMBOBOX == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        _dwComboIndex = dwSelectedItem;
        hr = S_OK;
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

// Khi nguoi dung bam vao link "Quet lai khuon mat FaceID (Camera)" tren man hinh Win + L
HRESULT CSampleCredential::CommandLinkClicked(DWORD dwFieldID)
{
    HRESULT hr = S_OK;

    if (dwFieldID < ARRAYSIZE(_rgCredProvFieldDescriptors) &&
        (CPFT_COMMAND_LINK == _rgCredProvFieldDescriptors[dwFieldID].cpft))
    {
        if (dwFieldID == SFI_LAUNCHWINDOW_LINK)
        {
            UpdateStatusText(L"Đang khởi động camera quét lại...");
            StopWorkerThread();
            StartWorkerThread();
        }
    }
    else
    {
        hr = E_INVALIDARG;
    }

    return hr;
}

// Dong goi thong tin dang nhap (AutoLogon tu FaceID hoac Mat khau 123456 nguoi dung nhap)
HRESULT CSampleCredential::GetSerialization(_Out_ CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE *pcpgsr,
                                            _Out_ CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION *pcpcs,
                                            _Outptr_result_maybenull_ PWSTR *ppwszOptionalStatusText,
                                            _Out_ CREDENTIAL_PROVIDER_STATUS_ICON *pcpsiOptionalStatusIcon)
{
    HRESULT hr = E_UNEXPECTED;
    *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
    *ppwszOptionalStatusText = nullptr;
    *pcpsiOptionalStatusIcon = CPSI_NONE;
    ZeroMemory(pcpcs, sizeof(*pcpcs));

    // Neu o password trong (nguoi dung bam Enter de quet FaceID)
    if (_rgFieldStrings[SFI_PASSWORD] == nullptr || _rgFieldStrings[SFI_PASSWORD][0] == L'\0')
    {
        // Kiem tra nhanh neu dich vu Daemon dang tat thi yeu cau nhap mat khau ngay, khong bao dang bat camera
        if (!IsFaceIDDaemonAliveQuick())
        {
            UpdateStatusText(L"Dịch vụ VinFace đang tắt. Vui lòng nhập mật khẩu mở máy");
            *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
            return S_OK;
        }

        // Neu chua khoi chay luong quet ngam, kich hoat ngay lap tuc tren Worker Thread
        if (!_fWorkerRunning)
        {
            UpdateStatusText(L"Đang khởi động camera quét khuôn mặt...");
            StartWorkerThread();
            *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
            return S_OK;
        }
        else
        {
            // Neu worker thread dang chay do da bam Enter truoc do, cho phep no tiep tuc quet
            *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
            return S_OK;
        }
    }

    PCWSTR pwzPassToSend = _rgFieldStrings[SFI_PASSWORD];
    wchar_t szCustomPw[128] = {0};
    GetDynamicFallbackPassword(szCustomPw, ARRAYSIZE(szCustomPw));
    if (szCustomPw[0] != L'\0' && wcscmp(_rgFieldStrings[SFI_PASSWORD], szCustomPw) == 0) { pwzPassToSend = FACEID_DEFAULT_PASSWORD; }

    if (_fIsLocalUser)
    {
        PWSTR pwzProtectedPassword;
        hr = ProtectIfNecessaryAndCopyPassword(pwzPassToSend, _cpus, &pwzProtectedPassword);
        if (SUCCEEDED(hr))
        {
            PWSTR pszDomain;
            PWSTR pszUsername;
            hr = SplitDomainAndUsername(_pszQualifiedUserName, &pszDomain, &pszUsername);
            if (SUCCEEDED(hr))
            {
                KERB_INTERACTIVE_UNLOCK_LOGON kiul;
                hr = KerbInteractiveUnlockLogonInit(pszDomain, pszUsername, pwzProtectedPassword, _cpus, &kiul);
                if (SUCCEEDED(hr))
                {
                    hr = KerbInteractiveUnlockLogonPack(kiul, &pcpcs->rgbSerialization, &pcpcs->cbSerialization);
                    if (SUCCEEDED(hr))
                    {
                        ULONG ulAuthPackage;
                        hr = RetrieveNegotiateAuthPackage(&ulAuthPackage);
                        if (SUCCEEDED(hr))
                        {
                            pcpcs->ulAuthenticationPackage = ulAuthPackage;
                            pcpcs->clsidCredentialProvider = CLSID_CSample;
                            *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;
                        }
                    }
                }
                CoTaskMemFree(pszDomain);
                CoTaskMemFree(pszUsername);
            }
            CoTaskMemFree(pwzProtectedPassword);
        }
    }
    else
    {
        DWORD dwAuthFlags = CRED_PACK_PROTECTED_CREDENTIALS | CRED_PACK_ID_PROVIDER_CREDENTIALS;

        if (!CredPackAuthenticationBuffer(dwAuthFlags, _pszQualifiedUserName, const_cast<PWSTR>(_rgFieldStrings[SFI_PASSWORD]), nullptr, &pcpcs->cbSerialization) &&
            (GetLastError() == ERROR_INSUFFICIENT_BUFFER))
        {
            pcpcs->rgbSerialization = static_cast<byte *>(CoTaskMemAlloc(pcpcs->cbSerialization));
            if (pcpcs->rgbSerialization != nullptr)
            {
                hr = S_OK;
                if (CredPackAuthenticationBuffer(dwAuthFlags, _pszQualifiedUserName, const_cast<PWSTR>(_rgFieldStrings[SFI_PASSWORD]), pcpcs->rgbSerialization, &pcpcs->cbSerialization))
                {
                    ULONG ulAuthPackage;
                    hr = RetrieveNegotiateAuthPackage(&ulAuthPackage);
                    if (SUCCEEDED(hr))
                    {
                        pcpcs->ulAuthenticationPackage = ulAuthPackage;
                        pcpcs->clsidCredentialProvider = CLSID_CSample;
                        *pcpgsr = CPGSR_RETURN_CREDENTIAL_FINISHED;
                    }
                }
                else
                {
                    hr = HRESULT_FROM_WIN32(GetLastError());
                    if (SUCCEEDED(hr))
                    {
                        hr = E_FAIL;
                    }
                }

                if (FAILED(hr))
                {
                    CoTaskMemFree(pcpcs->rgbSerialization);
                }
            }
            else
            {
                hr = E_OUTOFMEMORY;
            }
        }
    }
    return hr;
}

struct REPORT_RESULT_STATUS_INFO
{
    NTSTATUS ntsStatus;
    NTSTATUS ntsSubstatus;
    PWSTR     pwzMessage;
    CREDENTIAL_PROVIDER_STATUS_ICON cpsi;
};

static const REPORT_RESULT_STATUS_INFO s_rgLogonStatusInfo[] =
{
    { STATUS_LOGON_FAILURE, STATUS_SUCCESS, L"Mật khẩu không đúng. Vui lòng thử lại hoặc quét khuôn mặt.", CPSI_ERROR, },
    { STATUS_ACCOUNT_RESTRICTION, STATUS_ACCOUNT_DISABLED, L"Tài khoản đang bị vô hiệu hóa.", CPSI_WARNING },
};

HRESULT CSampleCredential::ReportResult(NTSTATUS ntsStatus,
                                        NTSTATUS ntsSubstatus,
                                        _Outptr_result_maybenull_ PWSTR *ppwszOptionalStatusText,
                                        _Out_ CREDENTIAL_PROVIDER_STATUS_ICON *pcpsiOptionalStatusIcon)
{
    *ppwszOptionalStatusText = nullptr;
    *pcpsiOptionalStatusIcon = CPSI_NONE;

    DWORD dwStatusInfo = (DWORD)-1;

    for (DWORD i = 0; i < ARRAYSIZE(s_rgLogonStatusInfo); i++)
    {
        if (s_rgLogonStatusInfo[i].ntsStatus == ntsStatus && s_rgLogonStatusInfo[i].ntsSubstatus == ntsSubstatus)
        {
            dwStatusInfo = i;
            break;
        }
    }

    if ((DWORD)-1 != dwStatusInfo)
    {
        if (SUCCEEDED(SHStrDupW(s_rgLogonStatusInfo[dwStatusInfo].pwzMessage, ppwszOptionalStatusText)))
        {
            *pcpsiOptionalStatusIcon = s_rgLogonStatusInfo[dwStatusInfo].cpsi;
        }
    }

    if (FAILED(HRESULT_FROM_NT(ntsStatus)))
    {
        if (_pCredProvCredentialEvents)
        {
            _pCredProvCredentialEvents->SetFieldString(this, SFI_PASSWORD, L"");
        }
    }

    return S_OK;
}

HRESULT CSampleCredential::GetUserSid(_Outptr_result_nullonfailure_ PWSTR *ppszSid)
{
    *ppszSid = nullptr;
    HRESULT hr = E_UNEXPECTED;
    if (_pszUserSid != nullptr)
    {
        hr = SHStrDupW(_pszUserSid, ppszSid);
    }
    return hr;
}

HRESULT CSampleCredential::GetFieldOptions(DWORD dwFieldID,
                                           _Out_ CREDENTIAL_PROVIDER_CREDENTIAL_FIELD_OPTIONS *pcpcfo)
{
    *pcpcfo = CPCFO_NONE;

    if (dwFieldID == SFI_PASSWORD)
    {
        *pcpcfo = CPCFO_ENABLE_PASSWORD_REVEAL;
    }
    else if (dwFieldID == SFI_TILEIMAGE)
    {
        *pcpcfo = CPCFO_ENABLE_TOUCH_KEYBOARD_AUTO_INVOKE;
    }

    return S_OK;
}
