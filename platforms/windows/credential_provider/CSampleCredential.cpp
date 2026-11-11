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

#define FACEID_FALLBACK_PASSWORD L"123456"
#define FACEID_DAEMON_HOST       "127.0.0.1"
#define FACEID_DAEMON_PORT       9876

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
        if (pwzStatusOut) StringCchCopyW(pwzStatusOut, cchStatusOut, L"Loi khoi tao Winsock -> Nhap mat khau 123456");
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
            StringCchCopyW(pwzStatusOut, cchStatusOut, L"FaceID Daemon chua bat -> Nhap mat khau 123456 de mo khoa");
        }
        return false;
    }

    // Da ket noi thanh cong toi Daemon!
    if (pCredential)
    {
        pCredential->UpdateStatusText(L"[FaceID] Dang nhan dien... Vui long nhin thang vao camera");
    }

    // Chuyen ve blocking mode voi timeout doc 7.0 giay (cho Daemon quet camera)
    u_long blockMode = 0;
    ioctlsocket(sock, FIONBIO, &blockMode);
    DWORD recvTimeoutMs = 7000;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&recvTimeoutMs, sizeof(recvTimeoutMs));
    DWORD sendTimeoutMs = 2000;
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, (const char*)&sendTimeoutMs, sizeof(sendTimeoutMs));

    const char* szService = (cpus == CPUS_LOGON) ? "windows-logon" : "windows-unlock";
    char szReq[256];
    snprintf(szReq, sizeof(szReq), "{\"action\": \"auth\", \"user\": \"%s\", \"service\": \"%s\"}\n", szUserUtf8, szService);
    if (send(sock, szReq, (int)strlen(szReq), 0) == SOCKET_ERROR)
    {
        closesocket(sock);
        WSACleanup();
        if (pwzStatusOut) StringCchCopyW(pwzStatusOut, cchStatusOut, L"Loi gui yeu cau FaceID -> Nhap mat khau 123456");
        return false;
    }

    char recvBuf[4096];
    bool authSuccess = false;
    while (true)
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
        recvBuf[n] = '\0';

        if (strstr(recvBuf, "\"status\": \"SUCCESS\"") || strstr(recvBuf, "\"status\":\"SUCCESS\""))
        {
            authSuccess = true;
            if (pwzStatusOut)
            {
                StringCchCopyW(pwzStatusOut, cchStatusOut, L"[FaceID] XAC THUC KHUON MAT THANH CONG! Dang mo khoa...");
            }
            break;
        }
        else if (strstr(recvBuf, "\"status\": \"LOCKED_OUT\"") || strstr(recvBuf, "\"status\":\"LOCKED_OUT\""))
        {
            if (pwzStatusOut)
            {
                StringCchCopyW(pwzStatusOut, cchStatusOut, L"[FaceID] Tam khoa do sai qua 3 lan -> Vui long nhap mat khau 123456");
            }
            break;
        }
        else if (strstr(recvBuf, "\"status\": \"CAMERA_BUSY\"") || strstr(recvBuf, "\"status\":\"CAMERA_BUSY\""))
        {
            if (pwzStatusOut)
            {
                StringCchCopyW(pwzStatusOut, cchStatusOut, L"[FaceID] Camera dang ban -> Vui long nhap mat khau 123456");
            }
            break;
        }
        else if (strstr(recvBuf, "\"status\": \"BYPASS\"") || strstr(recvBuf, "\"status\":\"BYPASS\""))
        {
            if (pwzStatusOut)
            {
                StringCchCopyW(pwzStatusOut, cchStatusOut, L"[FaceID] Cong tac FaceID dang TAT (Bypass) -> Vui long nhap mat khau 123456");
            }
            break;
        }
        else if (strstr(recvBuf, "\"status\":"))
        {
            if (pwzStatusOut)
            {
                StringCchCopyW(pwzStatusOut, cchStatusOut, L"[FaceID] Khong khop khuon mat -> Vui long nhap mat khau 123456");
            }
            break;
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

    wchar_t szStatus[256] = {0};
    bool bSuccess = QueryFaceIDDaemon(pThis, pThis->_pszQualifiedUserName, pThis->_cpus, szStatus, ARRAYSIZE(szStatus), pThis->_hStopEvent);

    if (pThis->IsStopRequested())
    {
        pThis->_fWorkerRunning = FALSE;
        pThis->Release();
        return 0;
    }

    if (bSuccess)
    {
        pThis->OnFaceIDSuccess();
    }
    else
    {
        if (szStatus[0] != L'\0')
        {
            pThis->UpdateStatusText(szStatus);
        }
    }

    pThis->_fWorkerRunning = FALSE;
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
    CoTaskMemFree(_rgFieldStrings[SFI_FULLNAME_TEXT]);
    SHStrDupW(pszText, &_rgFieldStrings[SFI_FULLNAME_TEXT]);
    LeaveCriticalSection(&_csLock);

    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, pszText);
    }
}

void CSampleCredential::OnFaceIDSuccess()
{
    EnterCriticalSection(&_csLock);
    CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
    SHStrDupW(FACEID_FALLBACK_PASSWORD, &_rgFieldStrings[SFI_PASSWORD]);

    CoTaskMemFree(_rgFieldStrings[SFI_FULLNAME_TEXT]);
    SHStrDupW(L"[FaceID] XAC THUC THANH CONG! Dang tu dong mo khoa...", &_rgFieldStrings[SFI_FULLNAME_TEXT]);
    LeaveCriticalSection(&_csLock);

    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->SetFieldString(this, SFI_PASSWORD, _rgFieldStrings[SFI_PASSWORD]);
        _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, _rgFieldStrings[SFI_FULLNAME_TEXT]);
    }

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
        hr = SHStrDupW(L"FaceID Hybrid", &_rgFieldStrings[SFI_LABEL]);
    }
    if (SUCCEEDED(hr))
    {
        hr = SHStrDupW(L"FaceID Hybrid Authentication", &_rgFieldStrings[SFI_LARGE_TEXT]);
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
        hr = SHStrDupW(L"Quet lai khuon mat FaceID (Camera)", &_rgFieldStrings[SFI_LAUNCHWINDOW_LINK]);
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
        PWSTR pszUserName = nullptr;
        pcpUser->GetStringValue(PKEY_Identity_UserName, &pszUserName);
        if (pszUserName != nullptr)
        {
            wchar_t szString[256];
            StringCchPrintfW(szString, ARRAYSIZE(szString), L"Tai khoan: %s | Nhin camera hoac nhap 123456", pszUserName);
            hr = SHStrDupW(szString, &_rgFieldStrings[SFI_FULLNAME_TEXT]);
            CoTaskMemFree(pszUserName);
        }
        else
        {
            hr = SHStrDupW(L"Nhin thang vao camera hoac nhap mat khau 123456", &_rgFieldStrings[SFI_FULLNAME_TEXT]);
        }
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

// Khi tile duoc chon tren man hinh LogonUI, bat dau tien trinh quet ngam
HRESULT CSampleCredential::SetSelected(_Out_ BOOL *pbAutoLogon)
{
    *pbAutoLogon = FALSE;

    // Neu mat khau da duoc dien (FaceID da xac thuc thanh cong truoc do) -> Set pbAutoLogon = TRUE ngay!
    if (_rgFieldStrings[SFI_PASSWORD] && _rgFieldStrings[SFI_PASSWORD][0] != L'\0')
    {
        *pbAutoLogon = TRUE;
        return S_OK;
    }

    if (!_fChecked)
    {
        _fChecked = TRUE;
        UpdateStatusText(L"[FaceID] Dang khoi dong camera... Vui long nhin thang vao man hinh");
        StartWorkerThread();
    }

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
        *pcpfs = _rgFieldStatePairs[dwFieldID].cpfs;
        *pcpfis = _rgFieldStatePairs[dwFieldID].cpfis;
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
            UpdateStatusText(L"[FaceID] Dang bat camera quet lai khuon mat...");
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

    // Neu o password trong
    if (_rgFieldStrings[SFI_PASSWORD] == nullptr || _rgFieldStrings[SFI_PASSWORD][0] == L'\0')
    {
        // Neu worker thread dang chay -> cho toi da 3.5 giay xem worker thread co hoan tat quet khong
        if (_fWorkerRunning)
        {
            for (int i = 0; i < 35; ++i)
            {
                Sleep(100);
                if (_rgFieldStrings[SFI_PASSWORD] && _rgFieldStrings[SFI_PASSWORD][0] != L'\0')
                {
                    break;
                }
                if (!_fWorkerRunning)
                {
                    break;
                }
            }
        }

        // Neu van trong -> goi QueryFaceIDDaemon truc tiep fallback
        if (_rgFieldStrings[SFI_PASSWORD] == nullptr || _rgFieldStrings[SFI_PASSWORD][0] == L'\0')
        {
            wchar_t szStatus[256] = {0};
            if (QueryFaceIDDaemon(this, _pszQualifiedUserName, _cpus, szStatus, ARRAYSIZE(szStatus), NULL))
            {
                EnterCriticalSection(&_csLock);
                CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
                SHStrDupW(FACEID_FALLBACK_PASSWORD, &_rgFieldStrings[SFI_PASSWORD]);
                LeaveCriticalSection(&_csLock);
            }
            else
            {
                SHStrDupW(szStatus[0] ? szStatus : L"Khong nhan dien duoc khuon mat. Vui long nhap mat khau 123456.", ppwszOptionalStatusText);
                *pcpsiOptionalStatusIcon = CPSI_WARNING;
                *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
                return S_OK;
            }
        }
    }

    if (_fIsLocalUser)
    {
        PWSTR pwzProtectedPassword;
        hr = ProtectIfNecessaryAndCopyPassword(_rgFieldStrings[SFI_PASSWORD], _cpus, &pwzProtectedPassword);
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
    { STATUS_LOGON_FAILURE, STATUS_SUCCESS, L"Mat khau khong dung (Vui long nhap 123456 hoac quet FaceID).", CPSI_ERROR, },
    { STATUS_ACCOUNT_RESTRICTION, STATUS_ACCOUNT_DISABLED, L"Tai khoan dang bi vo hieu hoa.", CPSI_WARNING },
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
