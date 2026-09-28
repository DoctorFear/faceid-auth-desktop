# FaceID Authentication System for Ubuntu Desktop

Hệ thống xác thực khuôn mặt sinh trắc học dạng Hybrid:
- System Service chạy ngầm ôm AI model qua systemd
- Giao tiếp IPC qua Unix Domain Socket (/run/faceid.sock)
- Tích hợp PAM module cho quy trình Login/Sudo
- Active Liveness Detection (Chống giả mạo bằng đo EAR chớp mắt)
- GUI Control App bật/tắt và quản lý khuôn mặt
