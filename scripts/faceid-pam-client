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

DEFAULT_SOCKET_PATH = "/run/faceid/faceid.sock"
DEFAULT_TCP_PORT = 9876


def get_pam_environment():
    """Lay thong tin user va service do Linux PAM truyen qua bien moi truong."""
    user = os.environ.get("PAM_USER", "")
    service = os.environ.get("PAM_SERVICE", "sudo")
    
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
    return user, service


def query_daemon(user: str, service: str = "sudo", action: str = "auth") -> dict:
    """Ket noi toi Daemon (Unix Socket tren Linux hoac TCP Loopback tren Windows)."""
    s = None
    try:
        if hasattr(socket, "AF_UNIX") and sys.platform != "win32" and os.path.exists(DEFAULT_SOCKET_PATH):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(DEFAULT_SOCKET_PATH)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", DEFAULT_TCP_PORT))

        s.settimeout(10.0)
        req = {"action": action, "user": user, "service": service}
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))

        data = s.recv(4096).decode("utf-8").strip()
        return json.loads(data)
    except Exception as e:
        return {"status": "UNAVAILABLE", "error": str(e)}
    finally:
        if s:
            s.close()


def main():
    user, service = get_pam_environment()
    
    # Gui lenh toi Daemon
    res = query_daemon(user, service)
    status = res.get("status", "FAILED")

    if status == "SUCCESS":
        # In thong bao ra stderr de hien thi tren Terminal cua user truoc khi vao sudo
        print(f"[FaceID] Xac thuc khuon mat thanh cong cho '{user}'!", file=sys.stderr)
        sys.exit(0)  # PAM_SUCCESS
    elif status == "LOCKED_OUT":
        rem = res.get("remaining_seconds", 45)
        print(f"[FaceID] Tai khoan dang bi khoa tam thoi ({rem}s) -> Fallback Password.", file=sys.stderr)
        sys.exit(1)
    elif status == "CAMERA_BUSY":
        print("[FaceID] Camera dang ban (Zoom/Meet) -> Chuyen sang nhap Password.", file=sys.stderr)
        sys.exit(1)
    else:
        # Cac truong hop FAILED, UNAVAILABLE, BYPASS -> Fallback password
        sys.exit(1)


if __name__ == "__main__":
    main()
