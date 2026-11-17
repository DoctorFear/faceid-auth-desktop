# TÀI LIỆU ĐẶC TẢ HỆ THỐNG NHẬT KÝ & LỊCH SỬ BẢO MẬT (USER AUDIT & ACTIVITY LOG)
**Dự án:** FaceID Auth Desktop  
**Mục tiêu:** Chuyển đổi từ Debug Log kỹ thuật nội bộ sang Lịch sử sự kiện trực quan chuẩn trải nghiệm người dùng (UX) cao cấp.

---

## 1. Vấn Đề Hiện Tại
- Log hiện tại (`data/daemon.log`) chứa ~70% rác debug nội bộ (`dYaw`, `dPitch`, vector `Cohort`, trọng số `Logistic Regression`, địa chỉ TCP Socket).
- Người dùng không quan tâm và không hiểu các thông số kỹ thuật này.
- Thông tin giá trị nhất đối với người dùng (Ai đã mở máy? Mở lúc mấy giờ? Có ai mở lén không?) bị chìm dưới hàng nghìn dòng debug.
- Giao diện hiện tại hiển thị thô sơ bằng 1 khung đen Terminal, lệch tông với phong cách Modern Dashboard.

---

## 2. Danh Mục Sự Kiện Dành Cho Người Dùng (14 Sự Kiện Chuẩn)

### Nhóm 1: Sự Kiện Xác Thực & Mở Máy (Authentication & Access)
1. **Mở khóa thành công (Unlock Success)**
   - **Mã sự kiện:** `AUTH_SUCCESS`
   - **Mức độ:** `SUCCESS` (Xanh lá)
   - **Dữ liệu ghi:** Thời gian, User Windows (`ADMIN`), Tên khuôn mặt khớp (`Ha Thai Son`), Độ tin cậy (`conf=99.7%`), Dịch vụ (`windows-unlock` / `windows-logon`).
   - **Hiển thị người dùng:** `Mở khóa thành công • Ha Thai Son (99.7%)`

2. **Từ chối mở khóa - Không khớp mặt (Face Mismatch / Unknown)**
   - **Mã sự kiện:** `AUTH_FAILED_UNKNOWN`
   - **Mức độ:** `WARNING` (Đỏ)
   - **Dữ liệu ghi:** Thời gian, User Windows, Số lần thử còn lại (`consecutive_failures`).
   - **Hiển thị người dùng:** `Từ chối mở khóa: Khuôn mặt lạ không có trong hệ thống (Còn lại X/3 lần thử)`

3. **Hết thời gian quét (Scan Timeout)**
   - **Mã sự kiện:** `AUTH_TIMEOUT`
   - **Mức độ:** `INFO` (Vàng)
   - **Dữ liệu ghi:** Thời gian, Thời gian chờ tối đa (8s).
   - **Hiển thị người dùng:** `Hết thời gian xác thực: Không nhận diện được khuôn mặt trước camera`

4. **Camera bận hoặc bị chiếm quyền (Camera Busy)**
   - **Mã sự kiện:** `CAMERA_BUSY`
   - **Mức độ:** `WARNING` (Cam)
   - **Dữ liệu ghi:** Thời gian, Ứng dụng/Tiến trình khác đang chiếm webcam.
   - **Hiển thị người dùng:** `Camera đang bị ứng dụng khác sử dụng • Chuyển sang mật mã dự phòng`

---

### Nhóm 2: Cảnh Báo An Ninh & Chống Giả Mạo (Security & Intrusion)
5. **Phát hiện tấn công giả mạo (Anti-Spoofing Detection)**
   - **Mã sự kiện:** `SPOOF_DETECTED`
   - **Mức độ:** `CRITICAL` (Đỏ đậm)
   - **Dữ liệu ghi:** Thời gian, Điểm giả mạo (MiniFASNet Passive PAD / Texture Check).
   - **Hiển thị người dùng:** `CẢNH BÁO: Phát hiện hình ảnh/màn hình giả mạo trước camera!`

6. **Khóa tạm thời do thử sai quá nhiều (Brute-Force Lockout)**
   - **Mã sự kiện:** `RATE_LIMIT_LOCKOUT`
   - **Mức độ:** `CRITICAL` (Đỏ đậm)
   - **Dữ liệu ghi:** Thời gian, Thời gian khóa tạm thời (`lockout_duration_sec = 45s`), Số lần sai liên tiếp.
   - **Hiển thị người dùng:** `Bảo vệ kích hoạt: Tạm khóa FaceID trong 45 giây do xác thực sai 3 lần liên tiếp`

7. **Phát hiện phần mềm can thiệp Camera ảo (Virtual Camera Hijack)**
   - **Mã sự kiện:** `VIRTUAL_CAM_BLOCKED`
   - **Mức độ:** `CRITICAL` (Đỏ)
   - **Dữ liệu ghi:** Thời gian, Tên driver camera ảo (OBS, ManyCam).
   - **Hiển thị người dùng:** `Bảo vệ an toàn: Chặn luồng camera ảo không hợp lệ`

8. **Đăng nhập bằng Mật mã dự phòng (Fallback Password Used)**
   - **Mã sự kiện:** `FALLBACK_PW_USED`
   - **Mức độ:** `INFO` (Xanh dương)
   - **Dữ liệu ghi:** Thời gian, Phương thức (Mật mã dự phòng PIN).
   - **Hiển thị người dùng:** `Đăng nhập máy tính bằng Mật mã dự phòng`

---

### Nhóm 3: Quản Lý Danh Tính Khuôn Mặt (Face Management Audit)
9. **Đăng ký khuôn mặt mới (Enroll New Face)**
   - **Mã sự kiện:** `FACE_ENROLLED`
   - **Mức độ:** `SUCCESS` (Xanh dương)
   - **Dữ liệu ghi:** Thời gian, Tên khuôn mặt mới, Số góc mặt nạp (`100/100`).
   - **Hiển thị người dùng:** `Đã thêm khuôn mặt mới vào hệ thống: 'Ha Thai Son'`

10. **Xóa khuôn mặt khỏi hệ thống (Delete Face)**
    - **Mã sự kiện:** `FACE_DELETED`
    - **Mức độ:** `WARNING` (Xám/Đỏ)
    - **Dữ liệu ghi:** Thời gian, Tên khuôn mặt bị xóa.
    - **Hiển thị người dùng:** `Đã xóa khuôn mặt: 'Ten_Nguoi_Dung' khỏi danh sách mở máy`

---

### Nhóm 4: Cấu Hình & Trạng Thái Hệ Thống (System Guard Audit)
11. **Bật / Tắt công tắc FaceID (Master Switch Toggle)**
    - **Mã sự kiện:** `FACEID_STATE_CHANGED`
    - **Mức độ:** `INFO` (Tím)
    - **Dữ liệu ghi:** Trạng thái (`ENABLED` hoặc `DISABLED / BYPASS`).
    - **Hiển thị người dùng:** `Dịch vụ bảo vệ FaceID đã được BẬT` hoặc `Đã TẮT (Chế độ Bypass)`

12. **Cài đặt / Gỡ bỏ tích hợp màn hình khóa (WinLock Hook)**
    - **Mã sự kiện:** `WINLOCK_STATUS_CHANGED`
    - **Mức độ:** `INFO` (Tím)
    - **Dữ liệu ghi:** Trạng thái (`INSTALLED` hoặc `UNINSTALLED`).
    - **Hiển thị người dùng:** `Đã cài đặt bảo vệ màn hình khóa Windows` hoặc `Đã gỡ bỏ bảo vệ màn hình khóa`

13. **Thay đổi Mật mã dự phòng (Fallback Password Changed)**
    - **Mã sự kiện:** `PASSWORD_UPDATED`
    - **Mức độ:** `INFO` (Xanh dương)
    - **Dữ liệu ghi:** Thời gian cập nhật mật mã dự phòng.
    - **Hiển thị người dùng:** `Mật mã dự phòng mở máy đã được thay đổi thành công`

14. **Thay đổi chính sách bảo mật (Security Policy Changed)**
    - **Mã sự kiện:** `POLICY_UPDATED`
    - **Mức độ:** `INFO` (Xám)
    - **Dữ liệu ghi:** Chế độ `Quét tức thì` hay `Chống giả mạo chủ động (Active Liveness)`.
    - **Hiển thị người dùng:** `Cập nhật cấu hình: Đã bật chế độ xác thực tức thì`

---

## 3. Kiến Trúc Lưu Trữ & Triển Khai Kỹ Thuật (Cho Nhóm Lập Trình)

### A. Định Dạng Lưu Trữ
- Lưu vào file JSON dạng append-only: `data/audit_history.json` (hoặc SQLite nhẹ `data/audit.db`).
- Mỗi bản ghi (Record) chuẩn schema:
```json
{
  "id": "evt_1794901234_abc",
  "timestamp": "2026-11-17T17:45:12+07:00",
  "category": "AUTH", 
  "event_type": "AUTH_SUCCESS",
  "level": "SUCCESS",
  "title": "Mở khóa thành công",
  "user": "ADMIN",
  "face_name": "Ha Thai Son",
  "confidence": 0.997,
  "service": "windows-unlock",
  "details": "Nhận diện khuôn mặt khớp chính xác (99.7%) qua camera DShow"
}
```

### B. Cơ Chế Thu Thập (Event Hooking)
- **Trong `core/server.py`:**
  - Tại hàm `handle_auth_request`: khi `best_decision.success` -> ghi `AUTH_SUCCESS`.
  - Khi fail / timeout -> ghi `AUTH_FAILED_UNKNOWN` hoặc `AUTH_TIMEOUT`.
  - Khi `is_real is False` -> ghi `SPOOF_DETECTED`.
  - Khi `lock_status.is_locked` -> ghi `RATE_LIMIT_LOCKOUT`.
- **Trong `gui/modern_gui.py`:**
  - Tại các hàm `enroll_face`, `delete_face`, `change_fallback_password`, `install_windows_lock`, `uninstall_windows_lock`, `toggle_master` -> gọi `record_audit_event(...)`.

### C. Thiết Kế Giao Diện Tab "Nhật Ký Hoạt Động" (`gui/web/index.html`)
- **Bảng Lịch Sử Sự Kiện (Activity Timeline/Table):**
  - Cột 1: Thời gian (Định dạng thân thiện: `17:45 - Hôm nay`, `14:20 - Hôm qua`, ...).
  - Cột 2: Loại sự kiện (Icon + Tiêu đề ngắn gọn).
  - Cột 3: Chi tiết (Tên người dùng, Tên khuôn mặt, Độ khớp % hoặc Lý do từ chối).
  - Cột 4: Trạng thái (Pill badge màu: Xanh lá / Đỏ / Vàng / Xanh dương).
- **Bộ lọc nhanh (Quick Filters):**
  - `[Tất cả]` | `[Mở khóa thành công]` | `[Bị từ chối / Cảnh báo]` | `[Quản trị & Cài đặt]`.
- **Nút "Xem nhật ký kỹ thuật" (Advanced Developer Logs):**
  - Đặt ở góc phải thu nhỏ, bấm vào mới bung rộng modal/console thô xem `daemon.log` khi lập trình viên cần tra cứu mã lỗi.
