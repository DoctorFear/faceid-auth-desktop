# Tài Liệu Cơ Chế Hoạt Động & Tích Hợp Windows (`Win + L` Credential Provider)

**Dự án:** FaceID Hybrid Authentication System (Ubuntu 24.04 & Windows Dual-Platform)  
**Cập nhật:** 28/10/2026  
**Mục đích tài liệu:** Ghi chép đầy đủ toàn bộ cơ chế làm việc với hệ điều hành Windows, quy trình biên dịch, cài đặt, đăng ký **Windows V2 Credential Provider (`Win + L`)** và cơ chế kết nối TCP Socket tới `daemon.py` để cả nhóm nắm rõ.

---

## 1. Tổng Quan Kiến Trúc Đăng Nhập Trên Windows vs Linux

Trong hệ thống **FaceID Hybrid**, phần lõi xử lý AI (`core/` và `daemon.py`) được giữ nguyên dùng chung cho cả 2 hệ điều hành, chỉ thay đổi tầng giao tiếp hệ điều hành (`platforms/`):

| Thành phần | Trên Ubuntu Linux (`platforms/linux/`) | Trên Windows (`platforms/windows/`) |
| :--- | :--- | :--- |
| **Tiến trình giao diện đăng nhập hệ điều hành** | `sudo`, `gdm-password`, `gnome-screensaver` (PAM Stack) | **`LogonUI.exe`** (Màn hình khóa `Win + L` / Đăng nhập Windows trên **Winlogon Secure Desktop**) |
| **Module cầu nối (Plugin gắn vào OS)** | `pam_exec.so` $\rightarrow$ `faceid_pam_client.py` / `pam_faceid.py` | **COM DLL 64-bit: `SampleV2CredentialProvider.dll`** (cài tại `C:\Windows\System32`) |
| **Giao thức IPC gọi vào Daemon** | Unix Domain Socket (`/run/faceid/faceid.sock`) | **TCP Loopback Socket (`127.0.0.1:9876`)** |
| **Tiến trình xử lý AI ngầm** | `daemon.py` (`systemd` service) | `daemon.py` (Windows Background Process / Service) |

---

## 2. Tại Sao Phải Dùng Windows Credential Provider (`SampleV2CredentialProvider.dll`)?

Khi người dùng bấm tổ hợp phím **`Win + L`**, Windows chuyển sang môi trường bảo mật cô lập gọi là **Secure Desktop (`Winlogon`)**. Mọi cửa sổ ứng dụng thông thường (như cửa sổ Python/OpenCV/PyQt6 của người dùng) đều không được phép hiển thị đè lên màn hình `Win + L`.

Để hiển thị giao diện xác thực trên màn hình `Win + L` và mở khóa Windows thật sự:
1. Tiến trình hệ thống `LogonUI.exe` quét khóa Registry:
   `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers`
2. Tìm thấy CLSID `{5fd3d285-0dd9-4362-8855-e0abaacd4af6}` trỏ tới file **`C:\Windows\System32\SampleV2CredentialProvider.dll`**.
3. `LogonUI.exe` nạp file `.dll` này vào bộ nhớ và gọi các interface COM chuẩn của Microsoft (`ICredentialProvider`, `ICredentialProviderCredential2`).

---

## 3. Cấu Trúc Thư Mục `platforms/windows/credential_provider/`

Mã nguồn được trích xuất từ bộ mẫu chính thức **`Windows-classic-samples/Samples/CredentialProvider/cpp`** của Microsoft và nâng cấp tại `platforms/windows/credential_provider/`:

| Tên file | Vai trò kỹ thuật |
| :--- | :--- |
| **`CSampleProvider.h` / `.cpp`** | Cài đặt interface `ICredentialProvider` và `ICredentialProviderSetUserArray`:<br>• `SetUsageScenario()`: Kích hoạt khi đăng nhập (`CPUS_LOGON`) và khi mở khóa `Win + L` (`CPUS_UNLOCK_WORKSTATION`).<br>• `GetCredentialCount()`: Đặt `*pdwDefault = 0` để ô đăng nhập **FaceID Hybrid** tự động được chọn mặc định ngay khi bấm `Win + L`.<br>• `_EnumerateCredentials()`: Lấy tài khoản người dùng hiện tại từ Windows (`ICredentialProviderUserArray`, ví dụ `ADMIN`) để tạo ô đăng nhập. |
| **`CSampleCredential.h` / `.cpp`** | Cài đặt interface `ICredentialProviderCredential2` kết nối trực tiếp với `daemon.py`:<br>• `QueryFaceIDDaemon()`: Hàm C++ dùng **Winsock2 (`ws2_32.dll`)** mở kết nối TCP tới `127.0.0.1:9876` (non-blocking connect timeout `400ms` để không bao giờ làm đơ màn hình `Win + L` nếu Daemon đang tắt), gửi gói JSON `{"action": "auth", "user": "ADMIN", "service": "windows-unlock"}`.<br>• `SetSelected(BOOL *pbAutoLogon)`: Tự động gọi `QueryFaceIDDaemon()` ngay khi màn hình `Win + L` hiển thị. Nếu `daemon.py` trả về `"SUCCESS"`, tự động gán mật khẩu `123456` vào bộ nhớ và bật `*pbAutoLogon = TRUE` để mở khóa Windows không chạm!<br>• `CommandLinkClicked()`: Khi người dùng bấm nút *"Quet lai khuon mat FaceID (Camera)"*, gọi lại `QueryFaceIDDaemon()` để quét lại.<br>• `GetSerialization()`: Nếu ô mật khẩu để trống mà nhấn `Enter`, tự động gọi `QueryFaceIDDaemon()`. Nếu người dùng gõ mật khẩu dự phòng (`123456`), đóng gói `KERB_INTERACTIVE_UNLOCK_LOGON` gửi cho `lsass.exe` mở khóa ngay. |
| **`common.h`** | Cấu hình các trường hiển thị gọn gàng trên màn hình `Win + L`: Ảnh đại diện, Tiêu đề `"FaceID Hybrid Authentication"`, Dòng trạng thái FaceID, Ô mật khẩu dự phòng (`123456`), Nút Submit và Nút *"Quet lai khuon mat FaceID (Camera)"*. |
| **`helpers.h` / `.cpp`** | Các hàm đóng gói bộ đệm xác thực Kerberos (`KerbInteractiveUnlockLogonInit`, `KerbInteractiveUnlockLogonPack`, `RetrieveNegotiateAuthPackage`). |
| **`Dll.h` / `.cpp` & `guid.cpp`** | Quản lý vòng đời COM Server (`DllGetClassObject`, `DllCanUnloadNow`, `CClassFactory`) với mã định danh `CLSID_CSample = {5fd3d285-0dd9-4362-8855-e0abaacd4af6}`. |
| **`register.reg` / `Unregister.reg`** | Đăng ký hoặc gỡ bỏ Credential Provider khỏi Registry của Windows. |

---

## 4. Nhật Ký Triển Khai Từng Bước

### Bước 1: Sửa lỗi tương thích Windows cho Core Python & Đăng ký khuôn mặt `ADMIN`
1. Cập nhật `USERNAME_REGEX` trong `core/fhs.py` và `scripts/enroll.py` (`^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$`) để chấp nhận tên tài khoản chữ hoa của Windows (`ADMIN`).
2. Thêm chính sách `"windows-unlock"` vào `config/policy.json` (`decision_threshold: 0.68`, `sim_threshold: 0.62`, `require_active_liveness: false`, `active_timeout_sec: 5.0`) để mở khóa `Win + L` nhanh gọn.
3. Chạy đăng ký 5 góc khuôn mặt cho tài khoản `ADMIN`:
   ```powershell
   .\venv\Scripts\Activate.ps1
   python scripts/enroll.py ADMIN
   ```
   $\rightarrow$ Sinh ra file `data/embeddings/ADMIN.npy` (5 Pose Prototypes 512-D).

### Bước 2: Biên dịch `SampleV2CredentialProvider.dll` (64-bit) kèm Winsock2
Do Windows 64-bit chạy `LogonUI.exe` ở chế độ 64-bit thuần (`C:\Windows\System32`), file `.dll` bắt buộc phải được biên dịch ở kiến trúc **x86_64 (64-bit)** và liên kết tĩnh thư viện chuẩn C++ (`-static-libgcc -static-libstdc++`) kèm thư viện mạng `-lws2_32`:

```powershell
$env:Path += ";$env:LOCALAPPDATA\Programs\w64devkit\bin"
cd C:\Users\ADMIN\faceid-auth-desktop\platforms\windows\credential_provider

windres.exe resources.rc -o resources.o

g++.exe -shared -O2 -Wno-write-strings `
    -DUNICODE -D_UNICODE -DWINVER=0x0603 -D_WIN32_WINNT=0x0603 `
    -D__in= -D__out= -D__deref_out= -D__override= `
    -DCRED_PACK_PROTECTED_CREDENTIALS=0x1 -DCRED_PACK_WOW_BUFFER=0x2 -DCRED_PACK_ID_PROVIDER_CREDENTIALS=0x4 `
    CSampleCredential.cpp CSampleProvider.cpp Dll.cpp guid.cpp helpers.cpp resources.o `
    samplev2credentialprovider.def `
    -o SampleV2CredentialProvider.dll `
    -lws2_32 -lshlwapi -lcredui -lsecur32 -lole32 -luuid -lgdi32 -lpropsys -static-libgcc -static-libstdc++
```

### Bước 3: Cài đặt mật khẩu `123456` và Cập nhật DLL vào `C:\Windows\System32`
Chạy file `.\install_win_lock.bat` tại thư mục gốc dự án (tự động xin quyền UAC Administrator) để:
1. Đặt mật khẩu tài khoản Windows `ADMIN` là `123456` (`net user ADMIN 123456`).
2. Copy `SampleV2CredentialProvider.dll` vào `C:\Windows\System32\SampleV2CredentialProvider.dll`.
3. Nạp khóa Registry từ `register.reg`.

---

## 5. Hướng Dẫn Vận Hành & Kiểm Thử Mở Khóa `Win + L` Bằng Khuôn Mặt Hoặc `123456`

### Bước chuẩn bị trước khi bấm `Win + L`:
Mở một cửa sổ Terminal tại `C:\Users\ADMIN\faceid-auth-desktop` và bật **FaceID Daemon** chạy ngầm:
```powershell
.\venv\Scripts\Activate.ps1
python daemon.py
```
*(Daemon sẽ nạp sẵn 3 model ONNX vào RAM và lắng nghe tại `127.0.0.1:9876`).*

### 3 Cách mở khóa khi bạn bấm `Win + L`:
1. **Cách 1 – Tự động quét khuôn mặt mở khóa (Auto Unlock):**
   - Bấm **`Win + L`**, sau đó nhấp chuột/phím cách để bật màn hình đăng nhập và nhìn vào Camera.
   - Ô **FaceID Hybrid Authentication** được chọn mặc định $\rightarrow$ tự động gọi `daemon.py` bật Camera quét mặt $\rightarrow$ Nhận diện đúng `ADMIN` sẽ **tự động mở khóa thẳng vào Windows**!
2. **Cách 2 – Bấm `Enter` (để trống mật khẩu) hoặc bấm *"Quet lai khuon mat FaceID"*:**
   - Nếu lần đầu bạn chưa kịp nhìn vào Camera, chỉ cần để trống ô mật khẩu và nhấn phím **`Enter`** (hoặc bấm dòng chữ *"Quet lai khuon mat FaceID (Camera)"*) $\rightarrow$ Hệ thống sẽ quét lại Camera ngay lập tức và mở khóa!
3. **Cách 3 – Nhập mật khẩu dự phòng `123456` (Fallback):**
   - Nếu `daemon.py` chưa bật, Camera bị che, hoặc không khớp khuôn mặt $\rightarrow$ Gõ **`123456`** vào ô mật khẩu và nhấn **`Enter`** để mở khóa vào Windows.

---

## 6. Cách Gỡ Bỏ Khi Không Sử Dụng
Chạy file sau tại thư mục gốc dự án để gỡ Credential Provider khỏi màn hình `Win + L`:
```powershell
.\uninstall_win_lock.bat
```

---

## 7. Kết Quả Kiểm Thử Thực Nghiệm Thực Tế Trên Windows (`Win + L`)

Hệ thống đã được kiểm thử thực tế với màn hình khóa **`Win + L` (`LogonUI.exe`)** gọi qua `SampleV2CredentialProvider.dll` tới `daemon.py` (`Service='windows-unlock'`).

### Log Kiểm Toán Thực Tế (Audit Log):
```text
================================================================================
  FACEID HYBRID DAEMON - PHIEN BAN 2.1.0 (SECURE EDITION)
  He dieu hanh phat hien: Windows (Dev Mode)
  Trang thai hoat dong   : [BAT / ENABLED]
  Che do van hanh        : STANDALONE_TEST
  Diem lang nghe IPC     : Port TCP 9876 (Local Loopback)
================================================================================
2026-10-28 00:19:04,650 [INFO] [FaceID-Daemon] [+] SHA-256 Integrity Verified OK: detector (face_detection_yunet.onnx)
2026-10-28 00:19:04,650 [INFO] [FaceID-Daemon] [+] SHA-256 Integrity Verified OK: anti_spoof (minifasnet_v2.onnx)
2026-10-28 00:19:04,787 [INFO] [FaceID-Daemon] [+] SHA-256 Integrity Verified OK: embedder (arcface.onnx)
2026-10-28 00:19:05,089 [INFO] [FaceID-Daemon] [+] Da nap thanh cong toan bo AI Models vao RAM trong 0.30s!
2026-10-28 00:19:05,089 [INFO] [FaceID-Daemon] [+] Lang nghe TCP Socket tai: 127.0.0.1:9876 (Windows Dev Mode)
2026-10-28 00:19:13,655 [INFO] [FaceID-Daemon] [AUDIT] Yeu cau xac thuc user='ADMIN' (Service='windows-unlock', PID=11228, Thresh=0.68)
2026-10-28 00:19:15,982 [INFO] [FaceID-Daemon] [*] Camera BAT thanh cong. Tien hanh quet khuon mat...
2026-10-28 00:19:16,239 [INFO] [FaceID-Daemon] [AUDIT] AUTH_SUCCESS user='ADMIN' svc='windows-unlock' conf=0.984
2026-10-28 00:19:32,853 [INFO] [FaceID-Daemon] [AUDIT] Yeu cau xac thuc user='ADMIN' (Service='windows-unlock', PID=11228, Thresh=0.68)
2026-10-28 00:19:34,091 [INFO] [FaceID-Daemon] [*] Camera BAT thanh cong. Tien hanh quet khuon mat...
2026-10-28 00:19:34,222 [INFO] [FaceID-Daemon] [AUDIT] AUTH_SUCCESS user='ADMIN' svc='windows-unlock' conf=0.996
2026-10-28 00:23:30,264 [INFO] [FaceID-Daemon] [AUDIT] Yeu cau xac thuc user='ADMIN' (Service='windows-unlock', PID=11228, Thresh=0.68)
2026-10-28 00:23:32,658 [INFO] [FaceID-Daemon] [*] Camera BAT thanh cong. Tien hanh quet khuon mat...
2026-10-28 00:23:32,986 [INFO] [FaceID-Daemon] [AUDIT] AUTH_SUCCESS user='ADMIN' svc='windows-unlock' conf=0.995
```

### Đánh Giá Số Liệu Thực Tế:
- **Thời gian nạp sẵn 3 Model AI vào RAM (Preloading):** `0.30 giây`
- **Thời gian nhận diện & mở khóa `Win + L` (sau khi Camera bật):** Chỉ từ **`0.13 giây` đến `0.32 giây`** (nhanh hơn nhiều so với mục tiêu `< 0.5 giây` trong kế hoạch).
- **Độ tin cậy tổng hợp (Fused Confidence Score):** Đạt ổn định từ **`98.4%` đến `99.6%`** (`0.984 – 0.996`).

---

## 8. Cơ Chế Bật / Tắt Daemon (Turn On / Turn Off Daemon) & Hot-Reload

Hệ thống đã được trang bị đầy đủ **2 tầng bật/tắt linh hoạt** (cả qua CLI `faceidctl`, file `.bat` một chạm và giao diện đồ họa `gui/app_gui.py`):

### 8.1. Tầng 1: Bật / Tắt Công Tắc Xác Thực FaceID Tức Thì (`Hot-Reload` không cần tắt Daemon)
Khi `daemon.py` đang chạy ngầm, bạn có thể tạm tắt hoặc bật lại tính năng quét mặt ngay lập tức qua giao thức IPC (`"action": "enable"` / `"action": "disable"`):
- **Tắt tạm thời FaceID (Chuyển sang dùng mật khẩu `123456` 100%, không bật Camera):**
  ```powershell
  python scripts/faceidctl off
  ```
- **Bật lại FaceID ngay lập tức:**
  ```powershell
  python scripts/faceidctl on
  ```

### 8.2. Tầng 2: Khởi Động / Dừng Hẳn Tiến Trình Ngầm `daemon.py` (`Start / Stop / Restart`)
Khi muốn khởi chạy ngầm `daemon.py` (không cần giữ cửa sổ Terminal) hoặc tắt hẳn tiến trình để giải phóng hoàn toàn RAM & cổng TCP `9876`:
- **Khởi động Daemon chạy ngầm (`Background Detached Process`, ghi log vào `data/daemon.log` và lưu PID tại `data/daemon.pid`):**
  ```powershell
  python scripts/faceidctl start
  ```
  *(Hoặc nhấp đúp file **`turn_on_daemon.bat`** tại thư mục gốc).*
- **Dừng hẳn Daemon (`Stop` qua IPC & giải phóng Port `9876`):**
  ```powershell
  python scripts/faceidctl stop
  ```
  *(Hoặc nhấp đúp file **`turn_off_daemon.bat`** tại thư mục gốc).*
- **Khởi động lại Daemon (`Restart`) & Kiểm tra trạng thái (`Status`):**
  ```powershell
  python scripts/faceidctl restart
  python scripts/faceidctl status
  ```
- **Giao diện Quản trị PyQt6 (`python gui/app_gui.py`):**
  Trên thanh tiêu đề có sẵn **2 nút bấm trực quan**:
  1. **`🟢 DAEMON: ĐANG CHẠY` / `⚫ DAEMON: ĐÃ TẮT`**: Bấm để khởi động hoặc dừng tiến trình ngầm `daemon.py`.
  2. **`🟢 FACEID: BẬT` / `🔴 FACEID: TẮT (BYPASS)`**: Bấm để bật/tắt công tắc quét mặt tức thì (Hot-reload).

### 8.3. Tự Động Chạy Ngầm Sau Khi Tắt Máy / Khởi Động Lại Windows (`Boot Auto-Start`)
Để `daemon.py` tự động chạy ngầm ngay cả sau khi bạn **Shut down / Restart máy**, hệ thống hỗ trợ đăng ký kép vào:
1. **Windows Task Scheduler (`FaceID_Hybrid_Daemon` với Trigger `AtStartup` & `AtLogOn`, quyền `SYSTEM`)**: Được cài tự động khi chạy `.\install_win_lock.bat` (có quyền Admin), giúp `pythonw.exe daemon.py` tự động chạy ngầm **ngay khi Windows vừa bật nguồn (trước cả màn hình đăng nhập đầu tiên)**.
2. **Windows Startup Registry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\FaceIDHybridDaemon`)**: Được bật/tắt tức thì không cần quyền Admin qua lệnh:
   ```powershell
   # Bật tự động chạy ngầm khi khởi động máy + khởi chạy Daemon ngay lập tức
   python scripts/faceidctl enable-autostart

   # Tắt tự động chạy ngầm khi khởi động máy
   python scripts/faceidctl disable-autostart
   ```
   *(Hoặc nhấp đúp file `turn_on_daemon.bat` để bật cả Daemon + Auto-Start, và `turn_off_daemon.bat` để tắt hẳn cả Daemon + Auto-Start).*

### 8.4. Phân Biệt Ngữ Cảnh Khóa Màn Hình (`Win + L`) vs Khởi Động Máy (`Logon`)
Trong `CSampleCredential.cpp`, hàm `QueryFaceIDDaemon()` tự động kiểm tra ngữ cảnh `CREDENTIAL_PROVIDER_USAGE_SCENARIO (_cpus)` của Windows:
- **`CPUS_UNLOCK_WORKSTATION` (`Win + L` khóa màn hình tạm thời):** Gửi `"service": "windows-unlock"` $\rightarrow$ Chỉ dùng **Passive Liveness (`MiniFASNetV2` + `2D FFT`)**, không bắt làm thử thách Active Liveness, mở khóa siêu tốc trong `~0.15s – 0.30s`.
- **`CPUS_LOGON` (Bật máy / Đăng nhập mới sau khi tắt máy hoặc Sign out):** Gửi `"service": "windows-logon"` $\rightarrow$ Tự động yêu cầu **Active Liveness** theo cấu hình trong `config/policy.json`.

---

## 9. Bảng Quản Lý Hệ Thống Đồ Họa Toàn Diện (`gui/app_gui.py` - FaceID Control Panel)

Toàn bộ các chức năng của hệ thống đã được tích hợp vào một **Bảng Quản Lý Đồ Họa (PyQt6 Control Panel)** tại `gui/app_gui.py`, đi kèm file chạy nhanh `open_dashboard.bat` và biểu tượng ngoài màn hình Desktop **`FaceID Control Panel.lnk`**.

### Cách mở Bảng Quản Lý:
- **Cách 1:** Nhấp đúp biểu tượng **`FaceID Control Panel`** ngay ngoài màn hình **Desktop**.
- **Cách 2:** Nhấp đúp file **`open_dashboard.bat`** trong thư mục dự án.
- **Cách 3:** Chạy từ Terminal: `python gui/app_gui.py`.

### 4 Phân Khu Chức Năng Trong Bảng Quản Lý (`gui/app_gui.py`):
1. **Thanh Công Tắc Nhanh Trên Cùng (Header Quick Toggles):**
   - **`🚀 AUTO-START: BẬT / TẮT`**: Bật/tắt chế độ tự động chạy ngầm Daemon khi khởi động máy Windows.
   - **`🟢 DAEMON: ĐANG CHẠY (PID ...)` / `⚫ DAEMON: ĐÃ TẮT`**: Bấm để khởi động ngầm hoặc dừng hẳn tiến trình `daemon.py`.
   - **`🟢 FACEID: BẬT` / `🔴 FACEID: TẮT (BYPASS)`**: Công tắc bật/tắt nhận diện khuôn mặt tức thì (`Hot-Reload`).
2. **Tab 1 – `🎛️ Bảng Điều Khiển & Cấu Hình`:**
   - Điều khiển chi tiết tiến trình Daemon (`Bật Daemon Ngầm`, `Khởi Động Lại Daemon`, `Tắt Hẳn Daemon`).
   - Quản lý tích hợp màn hình khóa Windows (`Cài đặt / Cập nhật DLL Win + L + Task ONSTART`, `Gỡ bỏ tích hợp Win + L`, `Test nhanh luồng windows-unlock`).
   - Chỉnh sửa trực quan **Chính sách bảo mật theo ngữ cảnh (`config/policy.json`)**: Tùy chỉnh bật/tắt **Active Liveness**, ngưỡng **Decision Threshold**, **ArcFace Similarity**, **Timeout** riêng biệt cho khi khóa màn hình (`Win + L`) và khi bật máy (`Boot Logon`), bấm **`💾 Lưu Cấu Hình & Áp Dụng Ngay (Hot-Reload)`** để áp dụng tức thì mà không cần khởi động lại Daemon.
3. **Tab 2 – `📊 Giám Sát Camera & AI Realtime`:**
   - Xem trực tiếp luồng Camera và toàn bộ chỉ số AI theo thời gian thực (`FPS`, `FIQA Sharpness`, `2D FFT Moiré`, `MiniFASNetV2 PAD`, `ArcFace Cosine`, `Fused Confidence`).
4. **Tab 3 – `👥 Quản Lý Khuôn Mặt Người Dùng` & Tab 4 – `📜 Nhật Ký Kiểm Toán (Audit Logs)`:**
   - Xem danh sách tài khoản đã đăng ký (`ADMIN`), bấm 1 nút để **Đăng ký mới 5 góc mặt (`enroll.py`)**, **Kiểm thử 1:1 (`verify_interactive.py`)**, hoặc **Xóa mẫu khuôn mặt**.
   - Tự động đọc và cập nhật thời gian thực nhật ký xác thực từ `data/daemon.log`.
