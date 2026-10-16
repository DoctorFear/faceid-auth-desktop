"""
PAM FaceID Authentication Module cho Linux (Ubuntu 24.04 PAM Stack)
Tuong thich voi /etc/pam.d/sudo, /etc/pam.d/gdm-password, /etc/pam.d/gnome-screensaver.

Quy tac van hanh quan trong:
1. Neu Camera ban (Device Busy): Tra ve PAM_AUTHINFO_UNAVAIL ngay lap tuc de PAM chuyen xuong password, KHONG LAM TREO TERMINAL.
2. Neu User bi khoa tam thoi (Rate Limit / Lockout): Tra ve PAM_AUTHINFO_UNAVAIL kem thong bao, khong khoi dong camera.
3. Neu xac thuc thanh cong: Tra ve PAM_SUCCESS.
"""

import sys
import json
import socket
import logging

# Cac ma tra ve chuan cua Linux PAM
PAM_SUCCESS = 0
PAM_AUTH_ERR = 7
PAM_USER_UNKNOWN = 10
PAM_MAXTRIES = 11
PAM_AUTHINFO_UNAVAIL = 9
PAM_IGNORE = 25

DEFAULT_SOCKET_PATH = "/run/faceid/faceid.sock"


def communicate_daemon(user: str, service: str = "sudo", socket_path: str = DEFAULT_SOCKET_PATH) -> dict:
    """Gui lenh xac thuc toi FaceID Daemon qua Unix Domain Socket."""
    try:
        if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(socket_path)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", 9876))

        s.settimeout(12.0)
        req = {"action": "auth", "user": user, "service": service}
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))

        data = s.recv(4096).decode("utf-8").strip()
        s.close()
        return json.loads(data)
    except FileNotFoundError:
        return {"status": "DAEMON_UNAVAILABLE", "message": "FaceID Daemon chua chay."}
    except (ConnectionRefusedError, socket.timeout):
        return {"status": "DAEMON_UNAVAILABLE", "message": "Khong the ket noi FaceID Daemon."}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def pam_sm_authenticate(pamh, flags, argv):
    """Entry point chuan duoc Linux PAM goi khi xac thuc (sudo, login, screensaver)."""
    try:
        user = pamh.get_user(None)
    except Exception:
        return PAM_USER_UNKNOWN

    if not user:
        return PAM_USER_UNKNOWN

    # Lay ten service PAM (vi du: 'sudo', 'gdm-password', 'polkit-1')
    service = "sudo"
    try:
        service = pamh.service
    except Exception:
        pass

    # Gui yeu cau toi FaceID Daemon
    res = communicate_daemon(user, service=service)
    status = res.get("status", "ERROR")

    # Truong hop 1: Xac thuc thanh cong
    if status == "SUCCESS":
        try:
            pamh.conversation(pamh.Message(pamh.PAM_TEXT_INFO, f"[FaceID] Xac thuc khuon mat thanh cong ({res.get('confidence', 1.0):.1%})."))
        except Exception:
            pass
        return PAM_SUCCESS

    # Truong hop 2: Camera ban hoac User bi khoa tam thoi hoac chua enroll
    # -> Tra ve PAM_AUTHINFO_UNAVAIL de Linux PAM lap tuc hien prompt hoi password ma khong gay freeze terminal
    if status in ("CAMERA_BUSY", "LOCKED_OUT", "NOT_ENROLLED", "DAEMON_UNAVAILABLE", "RATE_LIMITED"):
        msg = res.get("message", "Chuyen sang nhap mat khau.")
        try:
            pamh.conversation(pamh.Message(pamh.PAM_TEXT_INFO, f"[FaceID] {msg} -> Fallback Password."))
        except Exception:
            pass
        return PAM_AUTHINFO_UNAVAIL

    # Truong hop 3: Khuon mat khong khop hoac gian lan sinh trac hoc
    try:
        pamh.conversation(pamh.Message(pamh.PAM_ERROR_MSG, f"[FaceID] {res.get('message', 'Xac thuc that bai.')}"))
    except Exception:
        pass
    return PAM_AUTH_ERR


def pam_sm_setcred(pamh, flags, argv):
    """Thiet lap credential cho PAM session."""
    return PAM_SUCCESS
