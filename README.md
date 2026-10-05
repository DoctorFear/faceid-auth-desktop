```markdown
# FaceID Auth Desktop

Hệ thống xác thực khuôn mặt cục bộ hiệu năng cao, bảo mật và hỗ trợ chống giả mạo thời gian thực dựa trên pipeline ONNX Runtime.

---

## 1. Kiến trúc hệ thống

### Nhóm module xử lý lõi (`core/`)
- `core/detector.py` (YuNet - `face_detection_yunet.onnx`): Dò tìm khuôn mặt (bounding box) và trích xuất 5 điểm mốc đặc trưng (landmarks: 2 mắt, mũi, 2 khóe miệng).
- `core/fiqa.py` (Chất lượng ảnh & Pose Estimation): Sử dụng `cv2.solvePnP` (cờ `SOLVEPNP_EPNP`) và Laplacian để đo độ nét, ánh sáng và ước lượng 3 góc quay đầu (Yaw, Pitch, Roll).
- `core/embedder.py` (ArcFace - `arcface.onnx`): Trích xuất vector đặc trưng 512 chiều ($512\text{-d}$), chuẩn hóa $L_2$ để so khớp qua khoảng cách Cosine.
- `core/anti_spoof.py` (MiniFASNetV2 - `minifasnet_v2.onnx`): Phát hiện tấn công giả mạo (ảnh in, màn hình số).

### Kịch bản thực thi (`scripts/`)
- `scripts/enroll.py`: Quy trình đăng ký khuôn mặt 5 góc tự động (Thẳng $\rightarrow$ Trái $\rightarrow$ Phải $\rightarrow$ Ngước $\rightarrow$ Cúi). Dữ liệu được lưu dạng ma trận $5 \times 512$ vào `data/embeddings/<username>.npy` bằng cơ chế ghi nguyên tử (Atomic Write qua file tạm) và phân quyền an toàn `0600`.
- `scripts/verify_live.py`: Kịch bản kiểm thử nhận diện và chống giả mạo trực tiếp qua webcam.

### Luồng xử lý dữ liệu
```text
Camera Frame
     │
     ▼
[core/detector.py] ──> Tọa độ Box + 5 Landmarks
     │
     ├───> [core/fiqa.py] ───────> Kiểm tra nét/sáng + Tính góc Yaw/Pitch/Roll
     │                                    │
     │                                    ▼ (Góc chuẩn & ảnh nét)
     ├───> [core/embedder.py] ───> Trích xuất vector 512 chiều
     │                                    │
     │                                    ▼
     └───> [scripts/enroll.py] ──> Lưu ma trận 5x512 vào data/embeddings/<user>.npy

```

---

## 2. Cài đặt môi trường

### Khởi tạo môi trường ảo

Trên Windows (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1

```

Trên Linux:

```bash
python3 -m venv venv
source venv/bin/activate

```

### Cài đặt thư viện phụ thuộc

```bash
pip install -r requirements.txt

```

### Chuẩn bị trọng số mô hình

Đặt các file sau vào thư mục `models/`:

* `face_detection_yunet.onnx`
* `arcface.onnx`
* `minifasnet_v2.onnx`

---

## 3. Hướng dẫn sử dụng

### Đăng ký khuôn mặt mới (Enrollment)

Chạy lệnh đăng ký với tên tài khoản:

```bash
python scripts/enroll.py testuser

```

Giao diện sẽ tự động hướng dẫn 5 tư thế và tự chụp khi người dùng giữ đúng góc:

1. Nhìn thẳng vào camera
2. Quay nhẹ sang trái
3. Quay nhẹ sang phải
4. Hơi ngước cằm lên
5. Hơi cúi cằm xuống

### Kiểm thử nhận diện trực tiếp

```bash
python scripts/verify_live.py

```

* Khung **Xanh lá**: Người thật và khớp với danh tính đã đăng ký.
* Khung **Vàng**: Người thật nhưng chưa đăng ký (`Unknown`).
* Khung **Đỏ**: Phát hiện khuôn mặt giả mạo (`SPOOF`).
* Nhấn phím `q` để thoát.

```

```