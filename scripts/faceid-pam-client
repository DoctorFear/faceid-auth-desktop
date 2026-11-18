#!/usr/bin/env python3
"""
faceid-pam-client - Client xac thuc cho pam_exec.so tren Linux (Ubuntu 24.04 LTS / Python 3.12).
Tuong thich hoan hao ca tren Linux (Unix Domain Socket) va Windows (TCP Loopback) de test.

Quy tac ma thoat (Exit Codes) cho pam_exec:
  Exit 0 : Xac thuc thanh cong (PAM_SUCCESS)
  Exit 1 : That bai hoac tu choi (PAM fallback sang Password prompt)
"""

import os
import sys
import json
import socket
import time

DEFAULT_SOCKET_PATH = "/run/faceid/faceid.sock"
DEFAULT_TCP_PORT = 9876


def get_pam_environment():
    """Lay thong tin user va service do Linux PAM truyen qua bien moi truong."""
    user = os.environ.get("PAM_USER", "")
    service = os.environ.get("PAM_SERVICE", "")
    
    # Neu chay test bang tay tren Terminal (khong qua PAM)
    if not user:
        if len(sys.argv) > 1:
            user = sys.argv[1]
        else:
            try:
                import pwd
                user = pwd.getpwuid(os.getuid()).pw_name
            except Exception:
                user = os.environ.get("USERNAME", "test")
    if not service:
        if len(sys.argv) > 2:
            service = sys.argv[2]
        else:
            service = "sudo"

    # Tren Ubuntu / GDM: ca man hinh dang nhap khoi dong (boot) va man hinh khoa tam thoi
    # deu mac dinh dung service name la 'gdm-password'.
    # Ta phan biet thong minh tren Linux: Neu user da co active graphical session (wayland hoac x11),
    # thi day chinh la Man hinh Khoa Tam Thoi -> ap dung chinh sach 'gnome-screensaver' (Khoa tam thoi).
    if service == "gdm-password" and user and sys.platform != "win32":
        try:
            import subprocess
            out = subprocess.check_output(["loginctl", "list-sessions", "--no-legend"], text=True, timeout=0.8)
            for line in out.strip().splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[2] == user:
                    sid = parts[0]
                    s_info = subprocess.check_output(["loginctl", "show-session", sid, "-p", "Type", "-p", "Class"], text=True, timeout=0.5)
                    if "Class=user" in s_info and ("Type=wayland" in s_info or "Type=x11" in s_info):
                        service = "gnome-screensaver"
                        break
        except Exception:
            pass

    return user, service


def _connect_daemon_socket(timeout: float = 12.0) -> socket.socket:
    if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
        for sock_path in (DEFAULT_SOCKET_PATH, "/tmp/faceid.sock"):
            if os.path.exists(sock_path):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(timeout)
                    s.connect(sock_path)
                    return s
                except Exception:
                    try:
                        s.close()
                    except Exception:
                        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", DEFAULT_TCP_PORT))
    return s


def query_daemon(user: str, service: str = "sudo", action: str = "auth") -> dict:
    """Ket noi toi Daemon va lang nghe ca su kien huong dan liveness theo thoi gian thuc."""
    s = None
    printed_messages = set()
    try:
        if action == "auth":
            init_msg = "Đang khởi động camera quét khuôn mặt..."
            print(init_msg, flush=True)
            printed_messages.add(init_msg)

        s = _connect_daemon_socket(timeout=12.0)
        req = {"action": action, "user": user, "service": service}
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))

        sock_file = s.makefile("r", encoding="utf-8")
        for line in sock_file:
            line = line.strip()
            if not line:
                continue
            msg_obj = json.loads(line)
            # Neu la su kien tien do hoac thu thach liveness, in ra man hinh ngay
            if msg_obj.get("event") == "progress":
                msg_text = msg_obj.get("message", "")
                if msg_text and msg_text not in printed_messages:
                    printed_messages.add(msg_text)
                    print(msg_text, flush=True)
            elif "status" in msg_obj:
                msg_obj["_printed_messages"] = printed_messages
                return msg_obj

        return {"status": "FAILED", "fallback": "PASSWORD", "_printed_messages": printed_messages}
    except Exception as e:
        return {"status": "UNAVAILABLE", "error": str(e), "_printed_messages": printed_messages}
    finally:
        if s:
            s.close()


def main():
    user, service = get_pam_environment()
    
    # Gui lenh toi Daemon
    res = query_daemon(user, service)
    status = res.get("status", "FAILED")
    printed = res.get("_printed_messages") or set()

    if status == "SUCCESS":
        matched_user = res.get("matched_user") or user
        msg = f"Xác thực thành công ({matched_user})! Đang mở khóa..."
        if msg not in printed:
            print(msg, flush=True)

        # Gui Desktop Notification xac nhan khi vao Desktop
        try:
            import subprocess
            uid_str = os.environ.get("PAM_UID", "1000")
            dbus_addr = f"unix:path=/run/user/{uid_str}/bus"
            subprocess.Popen([
                "notify-send", "-a", "VinFace", "-i", "security-high",
                f"Xác thực thành công ({matched_user})",
                "Đã mở khóa máy tính an toàn."
            ], env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS=dbus_addr), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # Giu thong bao tren man hinh de nguoi dung doc ro truoc khi GDM chuyen huong vao Desktop
        if service in ("gdm-password", "gnome-screensaver"):
            time.sleep(1.5)
        else:
            time.sleep(0.3)
        sys.exit(0)  # PAM_SUCCESS
    elif status == "LOCKED_OUT":
        msg = "FaceID đã tạm khóa do thử sai nhiều lần. Vui lòng nhập mật khẩu"
        if msg not in printed:
            print(msg, flush=True)
        sys.exit(1)
    elif status in ("CAMERA_BUSY", "CAMERA_ERROR"):
        msg = res.get("message") or "Camera đang bận hoặc không thể mở. Vui lòng sử dụng mật mã dự phòng"
        if msg not in printed:
            print(msg, flush=True)
        sys.exit(1)
    elif status == "BYPASS":
        msg = "FaceID đang tạm tắt. Vui lòng nhập mật khẩu mở máy"
        if msg not in printed:
            print(msg, flush=True)
        sys.exit(1)
    elif status == "UNAVAILABLE":
        msg = "Không thể kết nối FaceID. Vui lòng nhập mật khẩu"
        if msg not in printed:
            print(msg, flush=True)
        sys.exit(1)
    elif status == "NOT_ENROLLED":
        msg = "Không nhận diện được khuôn mặt. Vui lòng nhập mật mã"
        if msg not in printed:
            print(msg, flush=True)
        sys.exit(1)
    else:
        # Cac truong hop FAILED -> Fallback password
        msg = res.get("message") or "Không nhận diện được khuôn mặt. Vui lòng nhập mật mã"
        if msg not in printed:
            print(msg, flush=True)
        sys.exit(1)


def handle_pwd_fail():
    """Khi nguoi dung nhap sai mat khau du phong:
    Neu ung dung FaceID dang BAT -> Hien thong bao huong dan tieng Viet.
    Neu ung dung dang TAT hoac Bypass -> Khong can thiep, giu nguyen nhu cu cua he thong.
    """
    try:
        res = query_daemon("nobody", action="status")
        if res.get("status") == "OK" and res.get("enabled", False):
            print("Mật khẩu không đúng. Vui lòng thử lại hoặc quét khuôn mặt.", flush=True)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--on-pwd-fail":
        handle_pwd_fail()
    main()
