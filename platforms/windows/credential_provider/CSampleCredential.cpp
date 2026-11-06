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
#include "guid.h"

#define FACEID_FALLBACK_PASSWORD L"123456"
#define FACEID_DAEMON_HOST       "127.0.0.1"
#define FACEID_DAEMON_PORT       9876

// Ket noi TCP Socket toi FaceID Daemon (127.0.0.1:9876) de yeu cau quet khuon mat
static bool QueryFaceIDDaemon(PCWSTR pwzQualifiedUser, CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, PWSTR pwzStatusOut, size_t cchStatusOut)
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

    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET)
    {
        WSACleanup();
        if (pwzStatusOut) StringCchCopyW(pwzStatusOut, cchStatusOut, L"Khong tao duoc socket -> Nhap mat khau 123456");
        return false;
    }

    // Thiet lap non-blocking connect voi timeout 400ms (de khong lam cham man hinh Win + L neu Daemon dang tat)
    u_long nonBlocking = 1;
    ioctlsocket(sock, FIONBIO, &nonBlocking);

    sockaddr_in addr;
    ZeroMemory(&addr, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(FACEID_DAEMON_PORT);
    addr.sin_addr.s_addr = inet_addr(FACEID_DAEMON_HOST);

    connect(sock, (sockaddr*)&addr, sizeof(addr));

    fd_set writeSet;
    FD_ZERO(&writeSet);
    FD_SET(sock, &writeSet);
    timeval tvConn;
    tvConn.tv_sec = 0;
    tvConn.tv_usec = 400000; // 400ms

    int sel = select(0, NULL, &writeSet, NULL, &tvConn);
    if (sel <= 0)
    {
        closesocket(sock);
        WSACleanup();
        if (pwzStatusOut)
        {
            StringCchCopyW(pwzStatusOut, cchStatusOut, L"FaceID Daemon chua bat -> Nhap mat khau 123456 de mo khoa");
        }
        return false;
    }

    // Chuyen ve blocking mode voi timeout doc 6.5 giay (cho Daemon quet camera)
    nonBlocking = 0;
    ioctlsocket(sock, FIONBIO, &nonBlocking);
    DWORD recvTimeoutMs = 6500;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&recvTimeoutMs, sizeof(recvTimeoutMs));
    DWORD sendTimeoutMs = 1500;
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

CSampleCredential::CSampleCredential():
    _cRef(1),
    _pCredProvCredentialEvents(nullptr),
    _pszUserSid(nullptr),
    _pszQualifiedUserName(nullptr),
    _fIsLocalUser(false),
    _fChecked(false),
    _fShowControls(false),
    _dwComboIndex(0)
{
    DllAddRef();

    ZeroMemory(_rgCredProvFieldDescriptors, sizeof(_rgCredProvFieldDescriptors));
    ZeroMemory(_rgFieldStatePairs, sizeof(_rgFieldStatePairs));
    ZeroMemory(_rgFieldStrings, sizeof(_rgFieldStrings));
}

CSampleCredential::~CSampleCredential()
{
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
    if (_pCredProvCredentialEvents)
    {
        _pCredProvCredentialEvents->Release();
    }
    _pCredProvCredentialEvents = nullptr;
    return S_OK;
}

// Khi nguoi dung bam Win + L va o FaceID duoc chon, tu dong goi sang daemon.py (127.0.0.1:9876)
HRESULT CSampleCredential::SetSelected(_Out_ BOOL *pbAutoLogon)
{
    *pbAutoLogon = FALSE;

    // Chi tu dong quet 1 lan khi tile vua duoc chon
    if (!_fChecked)
    {
        _fChecked = TRUE;
        wchar_t szStatus[256] = {0};
        if (QueryFaceIDDaemon(_pszQualifiedUserName, _cpus, szStatus, ARRAYSIZE(szStatus)))
        {
            // Neu FaceID Daemon xac thuc THANH CONG -> Tu dong dien mat khau 123456 va kich hoat AutoLogon!
            CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
            SHStrDupW(FACEID_FALLBACK_PASSWORD, &_rgFieldStrings[SFI_PASSWORD]);
            *pbAutoLogon = TRUE;
        }

        if (szStatus[0] != L'\0')
        {
            CoTaskMemFree(_rgFieldStrings[SFI_FULLNAME_TEXT]);
            SHStrDupW(szStatus, &_rgFieldStrings[SFI_FULLNAME_TEXT]);
            if (_pCredProvCredentialEvents)
            {
                _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, _rgFieldStrings[SFI_FULLNAME_TEXT]);
            }
        }
    }

    return S_OK;
}

HRESULT CSampleCredential::SetDeselected()
{
    HRESULT hr = S_OK;
    _fChecked = FALSE;
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
        hr = SHStrDupW(_rgFieldStrings[dwFieldID], ppwsz);
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
        PWSTR *ppwszStored = &_rgFieldStrings[dwFieldID];
        CoTaskMemFree(*ppwszStored);
        hr = SHStrDupW(pwz, ppwszStored);
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
            if (_pCredProvCredentialEvents)
            {
                _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, L"[FaceID] Dang bat camera quet khuon mat...");
            }

            wchar_t szStatus[256] = {0};
            if (QueryFaceIDDaemon(_pszQualifiedUserName, _cpus, szStatus, ARRAYSIZE(szStatus)))
            {
                CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
                SHStrDupW(FACEID_FALLBACK_PASSWORD, &_rgFieldStrings[SFI_PASSWORD]);
                if (_pCredProvCredentialEvents)
                {
                    _pCredProvCredentialEvents->SetFieldString(this, SFI_PASSWORD, _rgFieldStrings[SFI_PASSWORD]);
                    _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, L"[FaceID] XAC THUC THANH CONG! Bam Enter de vao Windows!");
                }
            }
            else
            {
                if (_pCredProvCredentialEvents && szStatus[0] != L'\0')
                {
                    _pCredProvCredentialEvents->SetFieldString(this, SFI_FULLNAME_TEXT, szStatus);
                }
            }
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

    // Neu nguoi dung nhan Enter khi o mat khau dang trong -> Thu goi FaceID Daemon quet khuon mat!
    if (_rgFieldStrings[SFI_PASSWORD] == nullptr || _rgFieldStrings[SFI_PASSWORD][0] == L'\0')
    {
        wchar_t szStatus[256] = {0};
        if (QueryFaceIDDaemon(_pszQualifiedUserName, _cpus, szStatus, ARRAYSIZE(szStatus)))
        {
            CoTaskMemFree(_rgFieldStrings[SFI_PASSWORD]);
            SHStrDupW(FACEID_FALLBACK_PASSWORD, &_rgFieldStrings[SFI_PASSWORD]);
        }
        else
        {
            SHStrDupW(szStatus[0] ? szStatus : L"Khong nhan dien duoc khuon mat. Vui long nhap mat khau 123456.", ppwszOptionalStatusText);
            *pcpsiOptionalStatusIcon = CPSI_WARNING;
            *pcpgsr = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
            return S_OK;
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
