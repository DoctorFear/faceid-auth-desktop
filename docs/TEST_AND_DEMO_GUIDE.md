# HƯỚNG DẪN KIỂM THỬ VÀ TRÌNH DIỄN DEMO (TEST & DEMO GUIDE)
**Dự án:** FaceID Hybrid Authentication System (Linux PAM & Windows Dev)  
**Phiên bản:** 2.1.0 (Secure Edition)  
**Tài liệu dành cho:** Người dùng, Tester, Giảng viên chấm thi & Buổi thuyết trình bảo vệ đồ án

---

## 📌 MỤC LỤC
1. [Chuẩn bị môi trường (Pre-requisites)](#1-chuẩn-bị-môi-trường)
2. [Kịch bản 1: Đăng ký khuôn mặt chuẩn 5 tư thế (Enrollment Demo)](#2-kịch-bản-1-đăng-ký-khuôn-mặt-chuẩn-5-tư-thế-enrollment)
3. [Kịch bản 2: Xác thực trực quan 1:1 thời gian thực (Interactive GUI Demo)](#3-kịch-bản-2-xác-thực-trực-quan-11-interactive-gui-demo)
4. [Kịch bản 3: Thử thách Chống Giả Mạo & Vân Moiré (PAD & Anti-Spoof Demo)](#4-kịch-bản-3-thử-thách-chống-giả-mạo--vân-moiré-pad-demo)
5. [Kịch bản 4: Trình diễn Daemon Server & Giao tiếp Socket (IPC Demo)](#5-kịch-bản-4-trình-diễn-daemon-server--giao-tiếp-socket-ipc-demo)
6. [Kịch bản 5: Đo kiểm An ninh, Chống Vét cạn & Fuzz Testing (Security Demo)](#6-kịch-bản-5-đo-kiểm-an-ninh-chống-vét-cạn--fuzz-testing-security-demo)
7. [Kịch bản 6: Thao tác Bật/Tắt và Quản trị bằng CLI `faceidctl`](#7-kịch-bản-6-thao-tác-bậttắt-và-quản-trị-bằng-cli-faceidctl)
8. [Bảng tổng hợp Lệnh Demo Nhanh (Cheat Sheet)](#8-bảng-tổng-hợp-lệnh-demo-nhanh-cheat-sheet)

---

## 1. CHUẨN BỊ MÔI TRƯỜNG

Trước khi chạy bất kỳ kịch bản nào, hãy mở Terminal tại thư mục gốc dự án `faceid-auth-desktop`:

### Trên Windows (PowerShell):
```powershell
# Di chuyển vào thư mục dự án
cd C:\Users\ADMIN\Desktop\faceid-auth-desktop

# Kích hoạt môi trường ảo Python
.\venv\Scripts\Activate.ps1
```

### Trên Ubuntu Linux (Bash):
```bash
cd ~/faceid-auth-desktop
source venv/bin/activate
```

> [!NOTE]
> Hệ thống hỗ trợ chế độ kép (**Dual-Platform**):
> - Trên **Windows**: Tự động dùng camera DirectShow và IPC qua cổng TCP loopback `127.0.0.1:9876`.
> - Trên **Linux**: Tự động dùng camera V4L2 `/dev/video0` và IPC qua Unix Domain Socket `/run/faceid/faceid.sock`.

---

## 2. KỊCH BẢN 1: ĐĂNG KÝ KHUÔN MẶT CHUẨN 5 TƯ THẾ (ENROLLMENT)

### Mục đích:
Chứng minh giải thuật thu thập khuôn mặt đa góc độ và áp dụng **Spherical K-Means** để nén 30 khung hình thu thập được thành đúng **5 Pose Prototypes** trên siêu cầu $\mathbb{S}^{511}$, triệt tiêu hoàn toàn lỗi **FAR Inflation**.

### Lệnh thực hiện:
```powershell
# Cú pháp: python scripts/enroll.py <tên_user_chữ_thường>
.\venv\Scripts\python.exe scripts/enroll.py son
```
*(Nếu muốn đăng ký user mới, thay `son` bằng `demo_user`)*.

### Các bước thao tác trước Camera:
Một cửa sổ OpenCV sẽ xuất hiện hướng dẫn người dùng qua 5 tư thế liên tiếp:
1. **Tư thế 1 (Thẳng):** Nhìn thẳng vào webcam cho đến khi thanh tiến trình đạt 100%.
2. **Tư thế 2 (Trái):** Quay mặt nhẹ sang bên trái ($15^\circ - 25^\circ$).
3. **Tư thế 3 (Phải):** Quay mặt nhẹ sang bên phải ($15^\circ - 25^\circ$).
4. **Tư thế 4 (Ngước):** Hơi ngước cằm lên trên.
5. **Tư thế 5 (Cúi):** Hơi cúi cằm xuống dưới.

### Kết quả quan sát:
- Terminal xuất thông báo trích xuất vector ArcFace 512 chiều.
- Dữ liệu được ghi nguyên tử (Atomic Write) phân quyền an toàn `0600` tại `data/embeddings/<user>.npy`.
- **Zero-Image Retention:** Không có bất kỳ ảnh mặt nào được ghi lại xuống ổ cứng.

---

## 3. KỊCH BẢN 2: XÁC THỰC TRỰC QUAN 1:1 (INTERACTIVE GUI DEMO)

### Mục đích:
Minh họa trực quan sinh động toàn bộ Pipeline AI hoạt động thời gian thực với bảng OSD HUD phân tích từng module (FPS, Detection, FIQA, FFT Moiré, PAD MiniFASNet, Liveness Cử động chủ động, ArcFace 512-D, và Fused Confidence).

### Lệnh thực hiện:
```powershell
.\venv\Scripts\python.exe scripts/verify_interactive.py --user son
```

### Các bước trình diễn:
1. **Quan sát Bảng điều khiển HUD trên cửa sổ Camera:**
   - **DET:** Tọa độ khuôn mặt & thời gian inference YuNet (~10ms).
   - **FIQA / FFT:** Đánh giá chất lượng ảnh & phổ tần số 2D FFT.
   - **PAD:** Xác suất mặt thật của MiniFASNet v2 (ví dụ: `PAD: 96%`).
   - **EMB:** Trích xuất ArcFace embedding (~60ms).
   - **FUS:** Điểm xác thực tổng hợp từ Logistic Regression.

2. **Thực hiện Thử thách Cử động Chủ động (Active Challenge-Response):**
   - Màn hình sẽ yêu cầu ngẫu nhiên một thử thách, ví dụ:
     - `CHOP MAT 2 CAI [o_o]x2` $\rightarrow$ Bạn chớp mắt 2 lần dứt khoát.
     - `MIEM CUOI :D` $\rightarrow$ Bạn nở nụ cười để thay đổi khoảng cách mép miệng.
     - `QUAY DAU SANG TRAI <-` $\rightarrow$ Xoay đầu nhẹ sang trái.
   - Khi hoàn thành thử thách, màn hình chuyển sang khung **XANH LÁ CÂY** kèm chữ:  
     `[SUCCESS] XAC THUC THANH CONG (Confidence: 99.8%)`.
3. Bấm phím **`q`** hoặc **`ESC`** trên cửa sổ camera để thoát.

---

## 4. KỊCH BẢN 3: THỬ THÁCH CHỐNG GIẢ MẠO & VÂN MOIRÉ (PAD DEMO)

### Mục đích:
Chứng minh hệ thống không thể bị đánh lừa bởi ảnh in trên giấy hoặc video phát lại trên màn hình điện thoại/máy tính bảng (đạt chuẩn ISO/IEC 30107-3).

### Cách thức kiểm thử 1: Tấn công bằng Màn hình Điện thoại (Replay Attack)
1. Giữ nguyên lệnh đang chạy: `python scripts/verify_interactive.py --user son`.
2. Mở điện thoại của bạn, hiển thị một bức ảnh khuôn mặt rõ nét của người dùng `son`.
3. Đưa màn hình điện thoại hướng thẳng vào webcam:
   - **Hiện tượng quan sát:** 
     - Thuật toán **2D FFT Moiré** lập tức cảnh báo `FFT: WARN` hoặc phát hiện vân sọc giao thoa tần số cao.
     - Mạng **MiniFASNet v2** trả điểm mặt thật cực thấp (`PAD: 1% - 15%`).
     - Khung nhận diện lập tức chuyển sang màu **ĐỎ**: `[SPOOF DETECTED] TU CHOI TRUY CAP`.
     - Thử thách cử động chủ động không thể vượt qua vì bức ảnh tĩnh không thể chớp mắt theo yêu cầu của hệ thống.

### Cách thức kiểm thử 2: Tấn công bằng Người Lạ (Imposter Attack)
1. Để một người khác (không phải `son`) ngồi trước webcam.
2. Dù người đó là người thật (PAD vượt qua 95%), khoảng cách Cosine trên siêu cầu $\mathbb{S}^{511}$ đối với 5 Pose Prototypes của `son` sẽ vượt ngưỡng an toàn ($D_{\cos} > 0.45$).
3. Điểm Fused Score sẽ dưới $0.20$ và hệ thống từ chối xác thực.

---

## 5. KỊCH BẢN 4: TRÌNH DIỄN DAEMON SERVER & GIAO TIẾP SOCKET (IPC DEMO)

### Mục đích:
Chứng minh kiến trúc **Daemon Client-Server**: Toàn bộ Model AI được nạp sẵn vào RAM từ trước (Preloading ~0.3s), khi có yêu cầu từ PAM (`sudo`), việc xác thực diễn ra siêu tốc dưới $0.4\text{s}$ mà không phải tốn thời gian khởi tạo lại model từ đầu.

### Bước 1: Mở Terminal 1 — Khởi động FaceID Daemon
```powershell
.\venv\Scripts\python.exe daemon.py
```
**Dấu hiệu Daemon chạy thành công:**
```text
================================================================================
  FACEID HYBRID DAEMON - PHIEN BAN 2.1.0 (SECURE EDITION)
  He dieu hanh phat hien: Windows (Dev Mode)
  Trang thai hoat dong   : [BAT / ENABLED]
  Che do van hanh        : STANDALONE_TEST
  Diem lang nghe IPC     : Port TCP 9876 (Local Loopback)
================================================================================
[+] SHA-256 Integrity Verified OK: detector, anti_spoof, embedder
[+] Da nap thanh cong toan bo AI Models vao RAM trong 0.31s!
[*] Lang nghe TCP Socket tai: 127.0.0.1:9876
```

### Bước 2: Mở Terminal 2 (Cửa sổ mới) — Giả lập yêu cầu xác thực từ PAM
Trong cửa sổ Terminal thứ 2, kích hoạt venv và chạy PAM Client:
```powershell
.\venv\Scripts\python.exe scripts/faceid_pam_client.py son sudo
```
* **Hiện tượng xảy ra:**
  - Daemon ở Terminal 1 nhận diện kết nối từ PID gọi, kiểm tra regex username `son`.
  - Camera bật đèn LED, quét khuôn mặt trong chưa đầy 1 giây.
  - Sau khi nhận diện xong, camera tự động giải phóng (tắt đèn LED).
  - Terminal 2 nhận kết quả: `{"status": "SUCCESS", "confidence": 0.98}` và trả exit code `0` (cho phép `sudo` thực thi).

### Bước 3: Thử nghiệm Fallback khi không có khuôn mặt
- Vẫn để Daemon chạy ở Terminal 1.
- Dùng tay che kín camera hoặc quay camera đi chỗ khác, chạy lại:
  ```powershell
  .\venv\Scripts\python.exe scripts/faceid_pam_client.py son sudo
  ```
- Sau 5 giây timeout không thấy mặt:
  - Daemon thông báo: `[AUDIT] AUTH_FAILED user='son' svc='sudo' (Con lai 2 lan)`.
  - Terminal 2 nhận `{"status": "FAILED", "fallback": "PASSWORD"}` và trả exit code `1`.
  - PAM trên Linux sẽ lập tức hiện prompt nhập mật khẩu `[sudo] password for son:`, không gây treo hệ thống.

---

## 6. KỊCH BẢN 5: ĐO KIỂM AN NINH, CHỐNG VÉT CẠN & FUZZ TESTING (SECURITY DEMO)

### Mục đích:
Chứng minh hệ thống đạt tiêu chuẩn an ninh cao cấp: Chống vét cạn (Lockout sau 3 lần sai), chống tấn công từ chối dịch vụ (DoS Flood), chống Path Traversal (`../../etc/passwd`), và socket không bao giờ bị sập (crash) khi nhận dữ liệu rác.

### Cách thức kiểm thử Fuzzing Socket:
Đảm bảo Daemon đang chạy ở Terminal 1, mở Terminal 2 và thực thi bộ kịch bản Fuzz Test tự động:
```powershell
.\venv\Scripts\python.exe eval/fuzz_socket.py
```

### Kết quả hiển thị đạt chuẩn:
```text
  BAT DAU FUZZ TESTING TREN SOCKET DAEMON
==================================================
  [PASS] Test 1: Malformed JSON syntax -> Daemon phan hoi an toan: {"status": "FAILED", "fallback": "PASSWORD"}
  [PASS] Test 2: Path Traversal Username -> Daemon phan hoi an toan: {"status": "FAILED", "fallback": "PASSWORD"}
  [PASS] Test 3: Shell Injection Characters -> Daemon phan hoi an toan: {"status": "FAILED", "fallback": "PASSWORD"}
  [PASS] Test 4: Oversized 100KB Payload -> Daemon phan hoi an toan: {"status": "FAILED", "fallback": "PASSWORD"}
  [PASS] Test 5: Non-UTF8 Binary Bytes -> Daemon phan hoi an toan: {"status": "FAILED", "fallback": "PASSWORD"}
  [*] Test 6: DoS Flood Protection (20 requests lien tuc)...
  [PASS] DoS Flood: Da chan thanh cong 17 requests qua nguong!

>>> KET QUA: DAEMON SONG SOT QUA MOI TEST, KHONG BI CRASH! <<<
```

### Kiểm chứng Cơ chế Khóa tài khoản (Rate Limiting & Lockout):
Nếu 1 tài khoản xác thực sai 3 lần liên tiếp:
- Lần 1 sai $\rightarrow$ `(Con lai 2 lan)`
- Lần 2 sai $\rightarrow$ `(Con lai 1 lan)`
- Lần 3 sai $\rightarrow$ `[LOCKOUT] User 'son' da bi khoa tam thoi 45 giay do fail 3 lan!`
- Trong 45 giây này, mọi request gửi tới cho user `son` đều bị từ chối ngay trong vòng **0.001 giây** mà không tốn CPU/GPU để bật camera.

---

## 7. KỊCH BẢN 6: THAO TÁC BẬT/TẮT VÀ QUẢN TRỊ BẰNG CLI `faceidctl`

### Mục đích:
Hướng dẫn sử dụng công cụ dòng lệnh duy nhất `faceidctl` để quản trị toàn bộ hoạt động của FaceID.

### 1. Kiểm tra trạng thái hệ thống:
```powershell
.\venv\Scripts\python.exe scripts/faceidctl status
```
*Hiển thị hệ điều hành hiện tại, công tắc bật/tắt, trạng thái socket daemon và camera.*

### 2. Tắt tính năng FaceID (Fallback 100% về mật khẩu):
```powershell
.\venv\Scripts\python.exe scripts/faceidctl off
```
*Lập tức cập nhật `config/config.json` sang `enabled: false`. Mọi yêu cầu xác thực sau đó sẽ chuyển thẳng sang mật khẩu truyền thống mà không đụng tới camera.*

### 3. Bật lại tính năng FaceID:
```powershell
.\venv\Scripts\python.exe scripts/faceidctl on
```

### 4. Liệt kê danh sách người dùng đã đăng ký FaceID:
```powershell
.\venv\Scripts\python.exe scripts/faceidctl list
```

---

## 8. BẢNG TỔNG HỢP LỆNH DEMO NHANH (CHEAT SHEET)

| STT | Kịch bản trình diễn | Lệnh chạy Terminal (Windows PowerShell) | Kết quả kỳ vọng |
|:---:|:---|:---|:---|
| **1** | **Đăng ký khuôn mặt 5 tư thế** | `.\venv\Scripts\python.exe scripts/enroll.py son` | Thu thập 5 góc, nén 5 Pose Prototypes vào `.npy` |
| **2** | **Test GUI Xác thực 1:1** | `.\venv\Scripts\python.exe scripts/verify_interactive.py --user son` | Hiện HUD FPS, PAD, Moiré, Chớp mắt, Khung Xanh |
| **3** | **Khởi chạy Daemon Server** | `.\venv\Scripts\python.exe daemon.py` | Verify SHA-256, nạp model vào RAM 0.3s, mở socket |
| **4** | **Test Client PAM gọi Daemon** | `.\venv\Scripts\python.exe scripts/faceid_pam_client.py son sudo` | Bật camera nhận diện, trả Exit code 0 / 1 |
| **5** | **Fuzz Testing & Chống Hack** | `.\venv\Scripts\python.exe eval/fuzz_socket.py` | Vượt qua 6 bài test độc hại, Daemon không crash |
| **6** | **Kiểm tra trạng thái CLI** | `.\venv\Scripts\python.exe scripts/faceidctl status` | Báo cáo OS, Enabled/Disabled, Daemon, Camera |
| **7** | **Bật / Tắt FaceID** | `.\venv\Scripts\python.exe scripts/faceidctl on` / `off` | Đổi trạng thái On/Off an toàn trong 1 giây |

---
*Tài liệu được biên soạn phục vụ công tác kiểm thử, chuyển giao công nghệ và bảo vệ đồ án tốt nghiệp FaceID Hybrid.*
