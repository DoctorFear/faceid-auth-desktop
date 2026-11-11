# TÀI LIỆU Ý NGHĨA & CẤU TRÚC TOÀN BỘ CÁC FILE TRONG DỰ ÁN FACEID
**Dự án:** FaceID Hybrid Authentication System (Hỗ trợ kép Linux Ubuntu 24.04 & Windows 10/11)  
**Ngày cập nhật:** 09/11/2026  
**Thư mục gốc:** `C:\Users\ADMIN\faceid-auth-desktop\`

---

## 1. Các File Kịch Bản & Khởi Động Ở Thư Mục Gốc

| Tên File / Thư Mục | Ý Nghĩa & Vai Trò Chi Tiết |
| :--- | :--- |
| **`daemon.py`** | Điểm khởi động chính (`entrypoint`) của FaceID Daemon chạy ngầm. Lắng nghe yêu cầu xác thực từ PAM (Linux) hoặc Credential Provider (Windows) qua Socket/TCP, nạp sẵn AI model vào RAM và điều phối camera. |
| **`open_dashboard.bat`** | File kịch bản khởi chạy nhanh Bảng điều khiển giao diện đồ họa (`gui/app_gui.py`) bằng `pythonw.exe` mà không bị giương cửa sổ đen dòng lệnh. |
| **`turn_on_daemon.bat`** | Kịch bản 1 chạm để kích hoạt tiến trình Daemon chạy ngầm và tự động bật đăng ký khởi động cùng máy tính (`enable-autostart`). |
| **`turn_off_daemon.bat`** | Kịch bản 1 chạm để tắt hẳn tiến trình Daemon chạy ngầm, giải phóng RAM, cổng TCP 9876 và chuyển sang chế độ mật khẩu dự phòng 100%. |
| **`install_win_lock.bat`** | Kịch bản cài đặt tự xin quyền Admin (UAC) để thiết lập mật khẩu `123456`, copy DLL Credential Provider vào `C:\Windows\System32`, nạp Registry và tạo Windows Scheduled Task chạy Daemon lúc vừa mở máy. |
| **`uninstall_win_lock.bat`** | Kịch bản gỡ bỏ module mở khóa màn hình `Win + L`, xóa DLL khỏi System32 và hủy đăng ký khỏi Registry của Windows. |
| **`access windows.md`** | Sổ tay ghi chép toàn bộ cơ chế kỹ thuật Windows: kiến trúc Winlogon/LogonUI, cách biên dịch DLL C++, cách Daemon xử lý socket `127.0.0.1:9876`, và nhật ký audit log thực tế. |
| **`requirements.txt`** | Danh sách các thư viện Python phụ thuộc cần thiết cho dự án (`onnxruntime`, `mediapipe`, `opencv-python`, `scikit-learn`, `PyQt6`, `numpy`,...). |
| **`README.md`** | Tài liệu giới thiệu tổng quan dự án, kiến trúc pipeline AI và hướng dẫn chạy nhanh. |
| **`.gitignore`** | Quy định các file và thư mục mà Git bỏ qua không đưa lên repository (như môi trường ảo `venv/`, cache Python, file binary lớn...). |

---

## 2. Thư Mục `config/` - Cấu Hình Hệ Thống

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`config/config.json`** | Cấu hình tham số cốt lõi của hệ thống: công tắc tổng (`enabled`), chế độ chạy (`mode`), đường dẫn socket/port TCP (9876), timeout camera, mã băm SHA-256 xác minh tính toàn vẹn của model ONNX, cấu hình Rate Limiting (chống spam/brute-force). |
| **`config/policy.json`** | Chính sách bảo mật theo ngữ cảnh (Contextual Security Policy). Định nghĩa ngưỡng tin cậy, ngưỡng tương đồng khuôn mặt, và quy định có cần kiểm tra cử động thật (`require_active_liveness`) riêng cho từng hành động (`windows-unlock` vs `windows-logon` vs `sudo`). |
| **`config/fusion_model.json`** | Chứa trọng số và hệ số bias đã được huấn luyện của mô hình Logistic Regression (Multi-Modal Fusion) để kết hợp điểm số giữa ArcFace, Anti-Spoofing, FIQA thành một điểm tin cậy tổng thể (AUC = 1.0). |

---

## 3. Thư Mục `core/` - Trí Tuệ Nhân Tạo & Xử Lý Cốt Lõi

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`core/detector.py`** | Module phát hiện khuôn mặt thời gian thực sử dụng mạng nơ-ron **YuNet** (`models/face_detection_yunet.onnx`). Trả về tọa độ bounding box, độ tin cậy và 5 điểm mốc đặc trưng (2 mắt, mũi, 2 khóe miệng). |
| **`core/fiqa.py`** | **Face Image Quality Assessment (FIQA)**: Đánh giá chất lượng ảnh khuôn mặt theo chuẩn ISO/IEC 29794-5 (độ sắc nét Laplacian, độ sáng, góc nghiêng khuôn mặt Yaw/Pitch/Roll) và phân tích phổ tần số Fourier 2D FFT để lọc bỏ hiện tượng sọc ma trận màn hình (Moiré pattern). |
| **`core/anti_spoof.py`** | **Presentation Attack Detection (PAD)**: Chống giả mạo khuôn mặt bị động (Passive Liveness) sử dụng model **MiniFASNetV2**. Nhận diện ảnh chụp từ điện thoại, iPad, mặt nạ in giấy và làm mượt điểm số qua chuỗi khung hình. |
| **`core/embedder.py`** | Module trích xuất vector đặc trưng danh tính khuôn mặt bằng kiến trúc **ArcFace** (ResNet-50 / 512 chiều). Chuẩn hóa L2 vector và tính toán khoảng cách cosine giữa khuôn mặt quét được và dữ liệu đã lưu. |
| **`core/liveness.py`** | **Active Liveness (Thử thách phản hồi - Challenge-Response)**: Bắt người dùng thực hiện các hành động ngẫu nhiên theo thời gian thực (chớp mắt, quay đầu trái/phải, ngửa/cúi mặt, vẫy tay) bằng MediaPipe Face Mesh & Hands để chống dùng video phát lại. |
| **`core/fusion.py`** | **Multi-Modal Fusion Engine**: Bộ hợp nhất đa phương thức kết hợp điểm sinh trắc học ArcFace (đã qua chuẩn hóa S-Norm với cohort dataset) + điểm chống giả mạo MiniFASNet + chỉ số chất lượng FIQA để đưa ra quyết định xác thực cuối cùng. |
| **`core/security.py`** | Quản lý an toàn hệ thống: Rate Limiter (khóa tài khoản tạm thời khi nhận diện sai quá 3 lần liên tiếp), chống tấn công làm nghẽn (DoS Flood Protection) và ghi nhận trạng thái Lockout. |
| **`core/server.py`** | **Lõi máy chủ IPC Daemon đa nền tảng**: Quản lý socket IPC (TCP 127.0.0.1:9876 trên Windows hoặc Unix Domain Socket trên Linux), kiểm tra tính toàn vẹn SHA-256 các model AI, Rate Limiting và so khớp đa tư thế (Top-2 Regularized). |
| **`core/fhs.py`** | Chuẩn hóa đường dẫn tập tin theo chuẩn FHS (Filesystem Hierarchy Standard) trên Linux hoặc thư mục cục bộ dự án trên Windows; kiểm tra chống lỗi bảo mật Path Traversal khi nhập tên người dùng. |

---

## 4. Thư Mục `data/` - Dữ Liệu Sinh Trắc Học & Nhật Ký

| File / Thư Mục | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`data/cohort_lfw.npy`** | Tập hợp 128 vector đặc trưng khuôn mặt mẫu từ tập dữ liệu LFW dùng cho thuật toán **S-Norm (Score Normalization)**, giúp cân bằng ngưỡng nhận diện và giảm thiểu tỷ lệ nhận nhầm (FAR). |
| **`data/embeddings/`** | Thư mục lưu trữ các vector khuôn mặt của người dùng đã đăng ký dưới dạng file `.npy` (Ví dụ: `ADMIN.npy` chứa 5 vector 512-D tương ứng với 5 góc nhìn: thẳng, trái, phải, ngửa, cúi). |
| **`data/daemon.log`** | File ghi nhật ký kiểm toán (Audit Log) thời gian thực của tiến trình Daemon: các lần kết nối IPC, trạng thái bật camera, độ tin cậy của các lượt mở khóa và các sự kiện bảo mật. |
| **`data/daemon.pid`** | File lưu mã định danh tiến trình (PID) của Daemon đang chạy ngầm, dùng để `faceidctl` theo dõi và dừng tiến trình chính xác. |
| **`data/datasets/`** | Thư mục dự phòng dùng để chứa các tập dữ liệu phục vụ huấn luyện mô hình hợp nhất (Fusion Engine). |

---

## 5. Thư Mục `models/` - Các Trọng Số AI Mạng Nơ-ron (ONNX)

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`models/face_detection_yunet.onnx`** | Trọng số mạng **YuNet** siêu nhẹ (~230 KB), được tối ưu để phát hiện khuôn mặt cực nhanh (vài mili-giây) trực tiếp trên CPU. |
| **`models/minifasnet_v2.onnx`** | Trọng số mạng **MiniFASNetV2** (~1.7 MB), chuyên phân tích kết cấu da, viền ảnh và phản xạ ánh sáng để phát hiện mặt giả/ảnh in/màn hình điện thoại. |
| **`models/arcface.onnx`** | Trọng số mạng **ArcFace** (~133 MB), mạng nơ-ron sâu chuyên trích xuất 512 thuộc tính nhận dạng danh tính khuôn mặt với độ chính xác cao. |

---

## 6. Thư Mục `platforms/` - Giao Tiếp Hệ Điều Hành (Windows & Linux)

### 6.1. Nhánh Windows (`platforms/windows/`)
| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`platforms/windows/camera_win.py`** | Trình quản lý camera chuyên biệt cho Windows sử dụng backend DirectShow / MSMF của OpenCV. |
| **`credential_provider/SampleV2CredentialProvider.dll`** | File thư viện động COM DLL 64-bit đã được biên dịch hoàn chỉnh từ C++, được nạp trực tiếp vào tiến trình `LogonUI.exe` của Windows khi bấm `Win + L`. |
| **`credential_provider/CSampleCredential.cpp` / `.h`** | Mã nguồn C++ xử lý logic đăng nhập trên màn hình khóa: mở socket Winsock2 gửi yêu cầu tới Daemon `127.0.0.1:9876`, nếu khuôn mặt hợp lệ sẽ tự động điền mật khẩu dự phòng `123456` và kích hoạt tự mở khóa Windows (`pbAutoLogon`). |
| **`credential_provider/CSampleProvider.cpp` / `.h`** | Mã nguồn C++ cài đặt interface `ICredentialProvider`, khởi tạo tile FaceID Hybrid trên màn hình Windows và ưu tiên chọn nó làm phương thức đăng nhập mặc định. |
| **`credential_provider/common.h`** | Định nghĩa các hằng số, định danh trường hiển thị (ảnh đại diện, ô mật khẩu, nhãn thông báo) trên màn hình đăng nhập Windows. |
| **`credential_provider/Dll.cpp` / `.h` & `guid.cpp` / `.h`** | Cài đặt chuẩn COM Server của Microsoft (`DllGetClassObject`, `IClassFactory`, GUID định danh Class ID của Credential Provider). |
| **`credential_provider/helpers.cpp` / `.h`** | Các hàm phụ trợ đóng gói gói tin xác thực Kerberos (`KERB_INTERACTIVE_UNLOCK_LOGON`) gửi tới hệ thống bảo mật Windows. |
| **`credential_provider/install_win_credential_provider.ps1`** | Kịch bản PowerShell tự động sao chép DLL vào `System32`, nhập khóa Registry và cấu hình Windows Task Scheduler khởi động ngầm cùng máy tính. |
| **`credential_provider/unregister_win_credential_provider.ps1`** | Kịch bản PowerShell dọn dẹp và gỡ bỏ Credential Provider khỏi Windows. |
| **`credential_provider/register.reg` & `Unregister.reg`** | Các file thiết lập ghi/xóa GUID của FaceID vào đường dẫn Registry của hệ điều hành Windows. |
| **`credential_provider/resources.rc`, `.o`, `resource.h`, `tileimage.bmp`** | Tài nguyên đồ họa (icon/hình ảnh hiển thị của FaceID) được nhúng vào file DLL khi biên dịch. |
| **`credential_provider/samplev2credentialprovider.def`** | File định nghĩa xuất hàm (Module Definition) chuẩn COM cho trình liên kết (Linker). |
| **`credential_provider/SampleV2CredentialProvider.sln` / `.vcxproj`** | File dự án Visual Studio C++ dùng để biên dịch hoặc bảo trì source code C++. |

### 6.2. Nhánh Linux (`platforms/linux/`)
| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`platforms/linux/socket_server.py`** | Module wrapper tương thích ngược (Backward Compatibility), nạp `core/server.py` để giữ tương thích với các script cũ. |
| **`platforms/linux/camera.py`** | Quản lý thiết bị Video4Linux (V4L2) trên Linux, tự động kiểm tra phần cứng `/sys/class/video4linux` để ngăn chặn camera ảo (OBS Virtual Cam, v4l2loopback). |
| **`platforms/linux/pam_faceid.py`** | Module cầu nối tương thích với chuẩn Linux Pluggable Authentication Modules (PAM). |
| **`platforms/linux/service/faceid-auth.service`** | File cấu hình `systemd service` để quản lý chạy Daemon ngầm và tự khởi động trên Ubuntu Linux. |
| **`platforms/linux/service/faceid.socket`** | File cấu hình kích hoạt theo socket (Socket Activation) của systemd trên Linux. |

---

## 7. Thư Mục `gui/` - Giao Diện Người Dùng

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`gui/app_gui.py`** | **Bảng Quản Lý Hệ Thống Toàn Diện (FaceID Control Panel)**: Viết bằng Python + PyQt6. Bao gồm 4 tab: Bảng điều khiển bật/tắt Daemon & cấu hình ngưỡng bảo mật, xem trực tiếp luồng camera & chỉ số AI (đã lật gương thuận mắt), quản lý danh sách người dùng, và xem nhật ký audit log. Chạy đa luồng không giật lag. |
| **`gui/lockscreen_ui.py`** | Giao diện màn hình khóa độc lập phụ trợ (dùng khi thử nghiệm màn hình khóa riêng trên Linux). |

---

## 8. Thư Mục `scripts/` - Công Cụ Dòng Lệnh & Kiểm Thử

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`scripts/faceidctl`** | Công cụ CLI quản lý hệ thống: `start`, `stop`, `restart`, `on`, `off`, `status`, `enable-autostart`, `disable-autostart`, `enroll`, `list`, `remove`. |
| **`scripts/enroll.py`** | Kịch bản hỗ trợ đăng ký khuôn mặt người dùng trực quan qua camera: tự động hướng dẫn chụp 5 góc (thẳng, trái, phải, ngửa, cúi) và lưu vector chuẩn hóa vào `data/embeddings/`. |
| **`scripts/verify_interactive.py`** | Kịch bản kiểm thử nhận diện 1:1 trực quan với thanh trạng thái FPS và luồng AI từng bước trên màn hình. |
| **`scripts/faceid_pam_client.py` & `faceid-pam-client`** | Client gửi request kiểm thử xác thực nhanh tới Daemon và lắng nghe kết quả trả về (Exit code 0 là thành công, 1 là fallback). |
| **`scripts/build_cohort.py`** | Kịch bản sinh bộ dữ liệu vector Cohort mẫu phục vụ thuật toán chuẩn hóa điểm số S-Norm. |
| **`scripts/run_pam_simulation.bat`** | File kịch bản Windows để chạy giả lập luồng gọi PAM/Credential Provider. |
| **`scripts/test_*.py`** (`test_camera.py`, `test_detector.py`, `test_cam_antispoof.py`, `test_cam_embedder.py`, `test_cam_fiqa.py`, `test_core.py`) | Bộ kịch bản unit test từng thành phần riêng lẻ: test webcam, test YuNet, test MiniFASNet, test ArcFace, test FIQA chất lượng ảnh. |

---

## 9. Thư Mục `eval/` - Đánh Giá & Huấn Luyện

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`eval/benchmark.py`** | Kịch bản đo lường hiệu năng và độ trễ (latency/FPS) từng chặng trong pipeline AI trên máy tính. |
| **`eval/train_fusion.py`** | Kịch bản huấn luyện mô hình Logistic Regression Fusion dựa trên tập dữ liệu đặc trưng đã thu thập. |
| **`eval/metrics.py`** | Tính toán các chỉ số sinh trắc học tiêu chuẩn: FAR (False Acceptance Rate), FRR (False Rejection Rate), EER (Equal Error Rate). |
| **`eval/fuzz_socket.py`** | Kịch bản kiểm thử bảo mật socket (Fuzz Testing): gửi các chuỗi JSON sai định dạng, chuỗi độc hại để kiểm tra khả năng chống sập của Daemon. |
| **`eval/roc_curve.png`** | Biểu đồ đường cong ROC (Receiver Operating Characteristic) thể hiện độ chính xác của hệ thống nhận diện. |

---

## 10. Thư Mục `docs/` - Tài Liệu Kỹ Thuật Dự Án

| File | Ý Nghĩa & Vai Trò |
| :--- | :--- |
| **`docs/ACADEMIC_RESEARCH_AND_THREAT_MODEL.md`** | **Báo cáo nghiên cứu học thuật chuyên sâu**: Phân tích mô hình đe dọa STRIDE chi tiết, đối sánh công nghệ với Howdy / Windows Hello / Apple Face ID, giải thích bản chất vật lý vì sao Webcam RGB yếu hơn Camera IR, cơ sở pháp lý Nghị định 13/2023/NĐ-CP & GDPR Điều 9, phân tích giới hạn tập kiểm thử và thiên lệch dữ liệu (Bias). |
| **`docs/ARCHITECTURE_AND_CODEBASE.md`** | Tài liệu chi tiết về kiến trúc phần mềm, mô hình phân tầng từ Core AI đến OS Interface. |
| **`docs/MATHEMATICAL_AND_TECHNICAL_SPECS.md`** | Tài liệu mô tả cơ sở toán học: công thức ArcFace Cosine, chuẩn hóa S-Norm, biến đổi Fourier 2D FFT, và hàm tối ưu hóa logistic fusion. |
| **`docs/TEST_AND_DEMO_GUIDE.md`** | Hướng dẫn các kịch bản kiểm thử hệ thống và demo thực tế. |
| **`docs/SYSTEM_GUIDE.html`** | Bản báo cáo tài liệu hệ thống định dạng HTML trực quan, có thể mở trực tiếp bằng trình duyệt web. |
