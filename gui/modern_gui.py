"""
FaceID Hybrid Modern Dashboard - PyWebView Application
Giao dien HTML5/Tailwind CSS sieu dep, chay tren nen Microsoft Edge Chromium WebView2.
"""

import os
import sys
import json
import time
import socket
import base64
import threading
import subprocess
import cv2
import numpy as np
import webview

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.fhs import get_fhs_path
from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.fusion import MultiModalFusionEngine
from core.liveness import ChallengeResponseDetector, ActionType
from core.constants import (
    MIN_FACE_SIZE_PX,
    STRICT_FRONTAL_MAX_YAW,
    STRICT_FRONTAL_MAX_PITCH,
    FRONTAL_STREAK_REQUIRED,
    POSE_RETURN_MAX_DYAW,
    POSE_RETURN_MAX_DPITCH,
    PAD_REAL_THRESHOLD,
    MOIRE_PASSIVE_PENALTY,
    MOIRE_FIQA_PENALTY,
    LOW_FIQA_PENALTY,
    TOP1_TEMPLATE_WEIGHT,
    TOP2_TEMPLATE_WEIGHT,
    DEFAULT_SIM_THRESHOLD,
    DEFAULT_DECISION_THRESHOLD,
    SECURITY_LEVEL_THRESHOLDS,
    DEFAULT_VERIFY_TIMEOUT_SEC,
    DEFAULT_CHALLENGE_TIMEOUT_SEC,
    DRILL_FAST_COUNTDOWN_SEC,
    DRILL_FAST_SCAN_WINDOW_SEC,
    INTERACTIVE_SCAN_WINDOW_SEC,
    DEFAULT_MAX_FAILURES,
    DEFAULT_LOCKOUT_SEC,
)
from scripts.enroll import load_all_enrolled_templates

CONFIG_FILE = get_fhs_path("config.json", category="config")
POLICY_FILE = get_fhs_path("policy.json", category="config")
EMB_DIR = get_fhs_path("embeddings", category="data")
LOG_FILE = get_fhs_path("daemon.log", category="data")
DEFAULT_SOCKET_PATH = "/run/faceid/faceid.sock"
DEFAULT_TCP_PORT = 9876


def fast_query_daemon(action: str = "status", timeout: float = 0.20) -> dict:
    s = None
    try:
        if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
            for sp in (DEFAULT_SOCKET_PATH, "/tmp/faceid.sock"):
                if os.path.exists(sp):
                    try:
                        cand = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        cand.settimeout(timeout)
                        cand.connect(sp)
                        s = cand
                        break
                    except Exception:
                        try:
                            cand.close()
                        except Exception:
                            pass
        if s is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(("127.0.0.1", DEFAULT_TCP_PORT))
        req = json.dumps({"action": action}) + "\n"
        s.sendall(req.encode("utf-8"))
        buf = s.recv(4096)
        if buf:
            return json.loads(buf.decode("utf-8").strip())
    except Exception:
        pass
    finally:
        if s:
            s.close()
    return {}


def _localize_hint(hint: str) -> str:
    if not hint:
        return hint
    return hint.strip()


import http.server
import socketserver

class _MJPEGHandler(http.server.BaseHTTPRequestHandler):
    api_ref = None
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        if self.path.startswith("/video"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            last_id = -1
            try:
                while _MJPEGHandler.api_ref and _MJPEGHandler.api_ref._cam_running:
                    jpg, fid = _MJPEGHandler.api_ref._latest_jpeg, _MJPEGHandler.api_ref._latest_frame_id
                    if jpg is not None and fid != last_id:
                        last_id = fid
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.flush()
                    else:
                        time.sleep(0.015)
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()

class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

class FaceIDApi:
    def __init__(self):
        self._window = None
        self._cam_running = False
        self._cam_thread = None
        self._cam_view_mode = "telemetry"  # "telemetry" hoac "interactive"
        self._target_user = "ALL"          # "ALL" hoac ten user cu the
        self._challenge_detector = None
        self._active_success = False
        self._active_success_time = 0.0
        self._challenge_hint = ""
        self._authenticated = False
        self._freeze_interactive = False
        self._frontal_streak = 0
        self._drill_fast_start_time = time.time()
        self._auth_conf = 0.0
        self._latest_jpeg = None
        self._latest_frame_id = 0
        self._ai_busy = False
        self._ai_cache = {"box": None, "landmarks": None, "metrics": None, "label": "", "color": (0, 255, 255)}
        self._mjpeg_port = 0
        try:
            _MJPEGHandler.api_ref = self
            self._mjpeg_srv = _ThreadedHTTPServer(("127.0.0.1", 0), _MJPEGHandler)
            self._mjpeg_port = self._mjpeg_srv.server_address[1]
            threading.Thread(target=self._mjpeg_srv.serve_forever, daemon=True).start()
        except Exception:
            self._mjpeg_port = 0

    def get_mjpeg_url(self):
        if self._mjpeg_port:
            return f"http://127.0.0.1:{self._mjpeg_port}/video"
        return ""

    def set_cam_view_mode(self, mode):
        self._freeze_interactive = False
        if mode in ("telemetry", "drill_fast", "interactive"):
            self._cam_view_mode = mode
            self.reset_challenge()
        return self._cam_view_mode

    def set_target_user(self, user):
        self._target_user = user or "ALL"
        self.reset_challenge()
        return self._target_user

    def reset_challenge(self):
        self._freeze_interactive = False
        self._frontal_streak = 0
        self._drill_fast_start_time = time.time()
        self._drill_scan_start_time = 0.0
        self._active_success = False
        self._active_success_time = 0.0
        self._authenticated = False
        self._auth_conf = 0.0
        if self._cam_view_mode == "drill_fast":
            self._challenge_hint = "Giữ thẳng khuôn mặt để mở khóa nhanh..."
            return
        self._challenge_hint = "Đang chuẩn bị thử thách cử động..."
        if self._challenge_detector:
            try:
                act = self._challenge_detector.reset_challenge()
                self._challenge_hint = act.value
            except Exception:
                pass
        return True

    def set_window(self, win):
        self._window = win

    def get_initial_data(self):
        status = fast_query_daemon("status")
        daemon_running = bool(status.get("pid"))
        enabled = status.get("enabled", True)

        cfg_data = {
            "cam_idx": 0,
            "fallback_pw": "123456",
            "timeout_sec": 8,
            "max_failures": 3,
            "lockout_sec": 45,
            "security_level": 1,
            "unlock_instant": True,
            "unlock_liveness": False,
            "logon_instant": False,
            "logon_liveness": True
        }

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    c = json.load(f)
                cfg_data["cam_idx"] = int(c.get("camera", {}).get("device_index", 0))
            except Exception:
                pass

        if os.path.exists(POLICY_FILE):
            try:
                with open(POLICY_FILE, "r", encoding="utf-8") as f:
                    full_pol = json.load(f)
                pol = full_pol.get("contextual_policies", {})
                u = pol.get("windows-unlock", {})
                l = pol.get("windows-logon", {})

                # uu tien doc truong auto_scan neu co, neu chua co thi suy tu require_active_liveness
                u_auto = u.get("auto_scan", not bool(u.get("require_active_liveness", False)))
                l_auto = l.get("auto_scan", not bool(l.get("require_active_liveness", True)))

                cfg_data["unlock_instant"] = bool(u_auto)
                cfg_data["unlock_liveness"] = bool(u.get("require_active_liveness", False))

                cfg_data["logon_instant"] = bool(l_auto)
                cfg_data["logon_liveness"] = bool(l.get("require_active_liveness", True))

                dec = float(l.get("decision_threshold", 0.68))
                if dec >= 0.72:
                    cfg_data["security_level"] = 2
                elif dec <= 0.65:
                    cfg_data["security_level"] = 0
                else:
                    cfg_data["security_level"] = 1

                cfg_data["timeout_sec"] = int(round(float(l.get("active_timeout_sec", 8.0))))
                rl = full_pol.get("rate_limit", {})
                cfg_data["max_failures"] = int(rl.get("max_consecutive_failures", 3))
                cfg_data["lockout_sec"] = int(round(float(rl.get("lockout_duration_sec", 45.0))))
            except Exception:
                pass

        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid", 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, "FallbackPassword")
                if val:
                    cfg_data["fallback_pw"] = val
        except Exception:
            pass

        winlock_installed = False
        if sys.platform == "win32" and os.path.exists(r"C:\Windows\System32\SampleV2CredentialProvider.dll"):
            try:
                import winreg
                reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{5fd3d285-0dd9-4362-8855-e0abaacd4af6}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ):
                    winlock_installed = True
            except Exception:
                pass
        elif sys.platform != "win32":
            winlock_installed = os.path.exists("/etc/faceid/pam_installed")

        return {
            "status": {
                "daemon_running": daemon_running,
                "enabled": enabled,
                "pid": status.get("pid", "?"),
                "winlock_installed": winlock_installed
            },
            "config": cfg_data
        }

    def toggle_master(self):
        st = fast_query_daemon("status")
        running = bool(st.get("pid"))
        enabled = st.get("enabled", True)

        ctl = os.path.join(BASE_DIR, "scripts", "faceidctl")
        if running and enabled:
            subprocess.run([sys.executable, ctl, "stop"], capture_output=True)
            return {"daemon_running": False, "enabled": False}
        else:
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        c = json.load(f)
                    c.setdefault("system", {})["enabled"] = True
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(c, f, indent=2)
                except Exception:
                    pass
            subprocess.run([sys.executable, ctl, "start"], capture_output=True)
            time.sleep(0.3)
            return {"daemon_running": True, "enabled": True}

    def daemon_ctl(self, action):
        ctl = os.path.join(BASE_DIR, "scripts", "faceidctl")
        if sys.platform == "win32":
            run_kw = {"capture_output": True, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
            if action == "start":
                subprocess.run([sys.executable, ctl, "enable-autostart"], **run_kw)
            elif action == "stop":
                subprocess.run([sys.executable, ctl, "disable-autostart"], **run_kw)
                subprocess.run([sys.executable, ctl, "stop"], **run_kw)
            else:
                subprocess.run([sys.executable, ctl, action], **run_kw)
        else:
            if action == "start":
                subprocess.run([sys.executable, ctl, "enable-autostart"], capture_output=True)
            elif action == "stop":
                subprocess.run([sys.executable, ctl, "disable-autostart"], capture_output=True)
                subprocess.run([sys.executable, ctl, "stop"], capture_output=True)
            else:
                subprocess.run([sys.executable, ctl, action], capture_output=True)
        time.sleep(0.3)
        st = fast_query_daemon("status")

        winlock_installed = False
        if sys.platform == "win32" and os.path.exists(r"C:\Windows\System32\SampleV2CredentialProvider.dll"):
            try:
                import winreg
                reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{5fd3d285-0dd9-4362-8855-e0abaacd4af6}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ):
                    winlock_installed = True
            except Exception:
                pass
        elif sys.platform != "win32":
            winlock_installed = os.path.exists("/etc/faceid/pam_installed")

        return {
            "daemon_running": bool(st.get("pid")),
            "enabled": st.get("enabled", True),
            "pid": st.get("pid", "?"),
            "winlock_installed": winlock_installed
        }

    def _record_audit_log(self, text):
        """Ghi nhat ky su kien truc tiep vao daemon.log de hien thi tren giao dien."""
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:23]
        line = f"{ts} [INFO] [FaceID-GUI] [AUDIT] {text}\n"
        for lf in (LOG_FILE, os.path.join(BASE_DIR, "data", "daemon.log")):
            try:
                os.makedirs(os.path.dirname(os.path.abspath(lf)), exist_ok=True)
                with open(lf, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass

    def save_settings(self, data):
        try:
            lvl = int(data.get("security_level", 1))
            lvl_cfg = SECURITY_LEVEL_THRESHOLDS.get(lvl, SECURITY_LEVEL_THRESHOLDS[1])
            dec_thresh = lvl_cfg["decision_threshold"]
            sim_thresh = lvl_cfg["sim_threshold"]

            tout = float(data.get("timeout_sec", DEFAULT_VERIFY_TIMEOUT_SEC))
            max_fail = max(1, min(20, int(data.get("max_failures", DEFAULT_MAX_FAILURES))))
            lockout_sec = max(5, min(600, int(data.get("lockout_sec", DEFAULT_LOCKOUT_SEC))))
            u_instant = bool(data.get("unlock_instant", True))
            l_instant = bool(data.get("logon_instant", False))
            u_live = bool(data.get("unlock_liveness", False)) if not u_instant else False
            l_live = bool(data.get("logon_liveness", True)) if not l_instant else False

            if os.path.exists(POLICY_FILE):
                with open(POLICY_FILE, "r", encoding="utf-8") as f:
                    pol = json.load(f)
                cp = pol.setdefault("contextual_policies", {})

                for svc_unlock in ("windows-unlock", "gnome-screensaver", "polkit-1"):
                    u_cfg = cp.setdefault(svc_unlock, {})
                    u_cfg["auto_scan"] = u_instant
                    u_cfg["require_active_liveness"] = u_live
                    u_cfg["decision_threshold"] = dec_thresh
                    u_cfg["sim_threshold"] = sim_thresh
                    u_cfg["active_timeout_sec"] = tout

                for svc_logon in ("windows-logon", "gdm-password", "sudo", "su"):
                    l_cfg = cp.setdefault(svc_logon, {})
                    l_cfg["auto_scan"] = l_instant
                    l_cfg["require_active_liveness"] = l_live
                    l_cfg["decision_threshold"] = dec_thresh
                    l_cfg["sim_threshold"] = sim_thresh
                    l_cfg["active_timeout_sec"] = tout
                pol.setdefault("rate_limit", {})["max_consecutive_failures"] = max_fail
                pol.setdefault("rate_limit", {})["lockout_duration_sec"] = float(lockout_sec)

                with open(POLICY_FILE, "w", encoding="utf-8") as f:
                    json.dump(pol, f, indent=2)

            cam_idx = max(0, int(data.get("cam_idx", 0)))
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg.setdefault("camera", {})["device_index"] = cam_idx
                cfg.setdefault("security", {}).setdefault("rate_limiting", {})["max_consecutive_failures"] = max_fail
                cfg.setdefault("security", {}).setdefault("rate_limiting", {})["lockout_duration_sec"] = float(lockout_sec)
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)

            # Chi cap nhat fallback_pw qua ham change_fallback_password rieng
            u_mode = "Quét tức thì" if u_instant else "Chống giả mạo chủ động"
            self._record_audit_log(f"POLICY_CHANGED: Cập nhật chính sách bảo mật (Cấp {lvl}, Mở khóa: {u_mode}, Timeout: {tout}s)")
            fast_query_daemon("reload", timeout=0.18)
            return True
        except Exception:
            return False

    def has_fallback_password(self):
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid", 0, winreg.KEY_READ) as k:
                    val, _ = winreg.QueryValueEx(k, "FallbackPassword")
                    return bool(str(val).strip())
            except Exception:
                return False
        return True

    def _write_fallback_pw_registry(self, new_pw):
        import winreg, ctypes
        try:
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid") as k:
                winreg.SetValueEx(k, "FallbackPassword", 0, winreg.REG_SZ, new_pw)
            return True
        except PermissionError:
            safe_pw = new_pw.replace('"', '\\"')
            args = f'add "HKLM\\SOFTWARE\\FaceIDHybrid" /v FallbackPassword /t REG_SZ /d "{safe_pw}" /f'
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "reg.exe", args, None, 0)
            if ret <= 32:
                return False
            for _ in range(15):
                time.sleep(0.4)
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid", 0, winreg.KEY_READ) as k:
                        val, _ = winreg.QueryValueEx(k, "FallbackPassword")
                        if str(val) == new_pw:
                            return True
                except Exception:
                    pass
            return False

    def _verify_windows_password(self, win_pw):
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            from ctypes import wintypes
            advapi32 = ctypes.windll.advapi32
            kernel32 = ctypes.windll.kernel32
            username = os.environ.get("USERNAME", "")
            hToken = wintypes.HANDLE()
            # LOGON32_LOGON_INTERACTIVE = 2, LOGON32_PROVIDER_DEFAULT = 0
            ok = advapi32.LogonUserW(username, ".", win_pw, 2, 0, ctypes.byref(hToken))
            if ok:
                kernel32.CloseHandle(hToken)
                return True
            return False
        except Exception:
            return False

    def has_windows_password(self):
        return not self._verify_windows_password("")

    def set_initial_fallback_password(self, new_pw, win_pw=""):
        try:
            new_pw = str(new_pw or "").strip()
            win_pw = str(win_pw or "")
            if not new_pw:
                return {"ok": False, "msg": "Mật khẩu không được để trống!"}
            if self.has_windows_password() and not self._verify_windows_password(win_pw):
                return {"ok": False, "msg": "Mật khẩu Windows không đúng!"}
            if sys.platform == "win32":
                if not self._write_fallback_pw_registry(new_pw):
                    return {"ok": False, "msg": "Bạn đã hủy hoặc không cấp quyền Administrator!"}
            else:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    cfg.setdefault("security", {})["fallback_password"] = new_pw
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2)
            return {"ok": True, "msg": "Đã thiết lập mật khẩu dự phòng!"}
        except Exception as e:
            return {"ok": False, "msg": f"Lỗi: {e}"}

    def change_fallback_password(self, old_pw, new_pw, win_pw=""):
        try:
            old_pw = str(old_pw or "").strip()
            new_pw = str(new_pw or "").strip()
            win_pw = str(win_pw or "")
            if not new_pw:
                return {"ok": False, "msg": "Mật khẩu mới không được để trống!"}
            if self.has_windows_password() and not self._verify_windows_password(win_pw):
                return {"ok": False, "msg": "Mật khẩu Windows không đúng!"}

            current_pw = "123456"
            if sys.platform == "win32":
                import winreg
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid", 0, winreg.KEY_READ) as k:
                        val, _ = winreg.QueryValueEx(k, "FallbackPassword")
                        if val:
                            current_pw = str(val)
                except Exception:
                    pass

                if old_pw != current_pw:
                    return {"ok": False, "msg": "Mật khẩu hiện tại không đúng!"}

                if not self._write_fallback_pw_registry(new_pw):
                    return {"ok": False, "msg": "Bạn đã hủy hoặc không cấp quyền Administrator!"}
            else:
                if os.path.exists(CONFIG_FILE):
                    try:
                        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                        current_pw = str(cfg.get("security", {}).get("fallback_password", "123456"))
                    except Exception:
                        pass
                if old_pw != current_pw:
                    return {"ok": False, "msg": "Mật khẩu hiện tại không đúng!"}
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    cfg.setdefault("security", {})["fallback_password"] = new_pw
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2)
            self._record_audit_log("PASSWORD_UPDATED: Mật mã dự phòng (PIN/Password) đã được thay đổi thành công")
            return {"ok": True, "msg": "Đã đổi mật khẩu dự phòng thành công!"}
        except Exception as e:
            return {"ok": False, "msg": f"Lỗi: {e}"}

    def check_winlock_status(self):
        if sys.platform != "win32":
            return os.path.exists("/etc/faceid/pam_installed")
        if sys.platform == "win32" and os.path.exists(r"C:\Windows\System32\SampleV2CredentialProvider.dll"):
            try:
                import winreg
                reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{5fd3d285-0dd9-4362-8855-e0abaacd4af6}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ):
                    return True
            except Exception:
                pass
        return False

    def _run_bat_hidden(self, bat_path):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c "{bat_path}"', BASE_DIR, 0)
            return True
        return False

    def install_windows_lock(self):
        if sys.platform == "win32":
            bat = os.path.join(BASE_DIR, "install_win_lock.bat")
            if os.path.exists(bat):
                self._run_bat_hidden(bat)
                self._record_audit_log("WINLOCK_INSTALLED: Đã cài đặt tích hợp bảo vệ màn hình khóa Windows")
                return True
            return False
        else:
            sh = os.path.abspath(os.path.join(BASE_DIR, "install_ubuntu_lock.sh"))
            if os.path.exists(sh):
                subprocess.Popen(["bash", sh], cwd=BASE_DIR, start_new_session=True)
                self._record_audit_log("WINLOCK_INSTALLED: Đã cài đặt tích hợp bảo vệ màn hình khóa Linux")
                return True
            return False

    def uninstall_windows_lock(self):
        self._record_audit_log("WINLOCK_UNINSTALLED: Đã gỡ bỏ bảo vệ màn hình khóa máy tính")
        if sys.platform == "win32":
            bat = os.path.join(BASE_DIR, "uninstall_win_lock.bat")
            if os.path.exists(bat):
                self._run_bat_hidden(bat)
                try:
                    if webview.windows:
                        webview.windows[0].hide()
                except Exception:
                    pass
                def _stop_and_exit():
                    try:
                        self.daemon_ctl("stop")
                    except Exception:
                        pass
                    os._exit(0)
                threading.Timer(1.0, _stop_and_exit).start()
                return True
            return False
        else:
            sh = os.path.abspath(os.path.join(BASE_DIR, "uninstall_ubuntu_lock.sh"))
            if os.path.exists(sh):
                subprocess.Popen(["bash", sh], cwd=BASE_DIR, start_new_session=True)
                return True
            return False

    def _get_emb_dirs(self):
        dirs = [EMB_DIR]
        local_emb = os.path.join(BASE_DIR, "data", "embeddings")
        if os.path.abspath(local_emb) != os.path.abspath(EMB_DIR):
            dirs.append(local_emb)
        return dirs

    def get_users(self):
        res = []
        seen = set()
        for d in self._get_emb_dirs():
            if os.path.exists(d):
                for f in sorted(os.listdir(d)):
                    if f.endswith(".npy"):
                        name = f[:-4]
                        if name in seen:
                            continue
                        seen.add(name)
                        p = os.path.join(d, f)
                        try:
                            arr = np.load(p, allow_pickle=False)
                            cnt = len(arr)
                        except Exception:
                            cnt = 0
                        res.append({"name": name, "count": cnt})
        return res

    def enroll_face(self, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "msg": "Tên không được để trống."}
        if any(c.isdigit() for c in name):
            return {"ok": False, "msg": "Tên khuôn mặt không được chứa chữ số."}
        
        current_users = self.get_users()
        if len(current_users) >= 5:
            return {"ok": False, "msg": "Hệ thống đã đạt giới hạn tối đa 5 khuôn mặt được phép mở máy."}
        
        existing_names = [u["name"].strip().lower() for u in current_users]
        if name.lower() in existing_names:
            return {"ok": False, "msg": f"Tên '{name}' đã tồn tại trong hệ thống."}

        script = os.path.join(BASE_DIR, "scripts", "enroll.py")
        popen_kw = {"cwd": BASE_DIR}
        if sys.platform == "win32":
            popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen([sys.executable, script, "--user", name], **popen_kw)
        self._record_audit_log(f"FACE_ENROLLED: Bắt đầu tiến trình ghi nhận khuôn mặt mới: '{name}'")
        return {"ok": True}

    def verify_interactive(self, name):
        if self._cam_running:
            self.stop_cam()
        script = os.path.join(BASE_DIR, "scripts", "verify_interactive.py")
        cmd = [sys.executable, script]
        if name:
            cmd += ["--user", name]
        popen_kw = {"cwd": BASE_DIR}
        if sys.platform == "win32":
            popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(cmd, **popen_kw)
        return True

    def delete_face(self, name):
        deleted = False
        for d in self._get_emb_dirs():
            p = os.path.join(d, f"{name}.npy")
            if os.path.exists(p):
                os.remove(p)
                deleted = True
        if deleted:
            self._record_audit_log(f"FACE_DELETED: Đã xóa khuôn mặt '{name}' khỏi danh sách mở khóa")
        return deleted

    def get_audit_events(self):
        """Doc va trich xuat cac su kien bao mat than thien voi nguoi dung tu nhat ky."""
        import re
        events = []
        current_face = None
        
        # Doc tu daemon.log
        for lf in (LOG_FILE, os.path.join(BASE_DIR, "data", "daemon.log")):
            if not os.path.exists(lf):
                continue
            try:
                with open(lf, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                m_time = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                ts = m_time.group(1) if m_time else ""

                m_face = re.search(r"Khop thanh cong voi khuon mat:\s*'([^']+)'\s*\(conf=([0-9.]+)\)", line)
                if m_face:
                    current_face = {"name": m_face.group(1), "conf": float(m_face.group(2))}
                    continue

                if "AUTH_SUCCESS" in line:
                    user_m = re.search(r"user='([^']+)'", line)
                    user = user_m.group(1) if user_m else "ADMIN"
                    conf_m = re.search(r"conf=([0-9.]+)", line)
                    conf_val = float(conf_m.group(1)) if conf_m else (current_face["conf"] if current_face else 0.99)
                    face_name = current_face["name"] if current_face else user
                    events.append({
                        "time": ts,
                        "type": "AUTH_SUCCESS",
                        "category": "auth",
                        "badge": "Thành công",
                        "badge_color": "emerald",
                        "icon": "fa-solid fa-circle-check",
                        "title": "Mở khóa máy tính thành công",
                        "details": f"Đã nhận diện khuôn mặt '{face_name}' • Độ tin cậy: {conf_val*100:.1f}%",
                        "user": user
                    })
                    current_face = None
                    continue

                if "AUTH_FAILED" in line:
                    user_m = re.search(r"user='([^']+)'", line)
                    user = user_m.group(1) if user_m else "ADMIN"
                    remain_m = re.search(r"Con lai\s*(\d+)\s*lan", line)
                    remain = f" • Còn {remain_m.group(1)} lần thử" if remain_m else ""
                    events.append({
                        "time": ts,
                        "type": "AUTH_FAILED",
                        "category": "auth",
                        "badge": "Bị từ chối",
                        "badge_color": "rose",
                        "icon": "fa-solid fa-circle-xmark",
                        "title": "Từ chối xác thực mở máy",
                        "details": f"Khuôn mặt trước camera không khớp với dữ liệu đã lưu{remain}",
                        "user": user
                    })
                    current_face = None
                    continue

                if "Tu choi do Lockout" in line or "bi khoa FaceID" in line:
                    events.append({
                        "time": ts,
                        "type": "LOCKOUT",
                        "category": "security",
                        "badge": "Khóa tạm thời",
                        "badge_color": "rose",
                        "icon": "fa-solid fa-shield-halved",
                        "title": "Kích hoạt khóa bảo vệ FaceID",
                        "details": "Tạm dừng mở khóa bằng khuôn mặt do xác thực thất bại quá 3 lần liên tiếp",
                        "user": "Hệ thống"
                    })
                    continue

                if "SPOOF" in line or "GIA MAO" in line or "VIRTUAL_CAMERA" in line:
                    events.append({
                        "time": ts,
                        "type": "SPOOF",
                        "category": "security",
                        "badge": "Cảnh báo an ninh",
                        "badge_color": "amber",
                        "icon": "fa-solid fa-triangle-exclamation",
                        "title": "Phát hiện dấu hiệu giả mạo",
                        "details": "Camera phát hiện hình ảnh màn hình điện thoại hoặc ảnh chụp không hợp lệ",
                        "user": "Bảo vệ"
                    })
                    continue

                if "BAT (ENABLED)" in line:
                    events.append({
                        "time": ts,
                        "type": "CONFIG",
                        "category": "system",
                        "badge": "Kích hoạt",
                        "badge_color": "sky",
                        "icon": "fa-solid fa-power-off",
                        "title": "Kích hoạt bảo vệ FaceID",
                        "details": "Dịch vụ FaceID đã được BẬT sẵn sàng mở khóa máy",
                        "user": "Hệ thống"
                    })
                    continue

                if "TAT (DISABLED" in line:
                    events.append({
                        "time": ts,
                        "type": "CONFIG",
                        "category": "system",
                        "badge": "Tạm dừng",
                        "badge_color": "slate",
                        "icon": "fa-solid fa-pause",
                        "title": "Tạm dừng bảo vệ FaceID",
                        "details": "Chế độ bảo vệ FaceID đã tạm tắt (Chế độ Bypass mở máy)",
                        "user": "Hệ thống"
                    })
                    continue

                if "CAMERA_BUSY" in line:
                    events.append({
                        "time": ts,
                        "type": "CAMERA_BUSY",
                        "category": "system",
                        "badge": "Camera bận",
                        "badge_color": "amber",
                        "icon": "fa-solid fa-video-slash",
                        "title": "Webcam đang bị ứng dụng khác chiếm",
                        "details": "Chuyển sang mở khóa bằng mật mã dự phòng",
                        "user": "Hệ thống"
                    })
                    continue

                if "AUTH_TIMEOUT" in line:
                    events.append({
                        "time": ts,
                        "type": "AUTH_TIMEOUT",
                        "category": "auth",
                        "badge": "Hết thời gian",
                        "badge_color": "amber",
                        "icon": "fa-solid fa-hourglass-end",
                        "title": "Hết thời gian quét khuôn mặt",
                        "details": "Không nhận diện được khuôn mặt trước camera sau thời gian chờ",
                        "user": "Hệ thống"
                    })
                    continue

                if "FALLBACK_PW_USED" in line:
                    events.append({
                        "time": ts,
                        "type": "FALLBACK_PW",
                        "category": "auth",
                        "badge": "Mật mã dự phòng",
                        "badge_color": "sky",
                        "icon": "fa-solid fa-key",
                        "title": "Đăng nhập bằng Mật mã dự phòng",
                        "details": "Người dùng đã mở máy bằng mã PIN / Mật mã dự phòng",
                        "user": "ADMIN"
                    })
                    continue

                if "FACE_ENROLLED" in line:
                    m_enr = re.search(r"FACE_ENROLLED:\s*(.*)", line)
                    msg_enr = m_enr.group(1) if m_enr else "Đăng ký khuôn mặt mới"
                    events.append({
                        "time": ts,
                        "type": "FACE_ENROLLED",
                        "category": "system",
                        "badge": "Thêm khuôn mặt",
                        "badge_color": "emerald",
                        "icon": "fa-solid fa-user-plus",
                        "title": "Quản lý khuôn mặt",
                        "details": msg_enr,
                        "user": "ADMIN"
                    })
                    continue

                if "FACE_DELETED" in line:
                    m_del = re.search(r"FACE_DELETED:\s*(.*)", line)
                    msg_del = m_del.group(1) if m_del else "Xóa khuôn mặt khỏi hệ thống"
                    events.append({
                        "time": ts,
                        "type": "FACE_DELETED",
                        "category": "system",
                        "badge": "Xóa khuôn mặt",
                        "badge_color": "rose",
                        "icon": "fa-solid fa-user-minus",
                        "title": "Quản lý khuôn mặt",
                        "details": msg_del,
                        "user": "ADMIN"
                    })
                    continue

                if "WINLOCK_INSTALLED" in line:
                    events.append({
                        "time": ts,
                        "type": "WINLOCK",
                        "category": "system",
                        "badge": "Cài đặt bảo vệ",
                        "badge_color": "emerald",
                        "icon": "fa-solid fa-lock",
                        "title": "Tích hợp màn hình khóa Windows",
                        "details": "Đã cài đặt FaceID bảo vệ trực tiếp màn hình khóa máy tính",
                        "user": "ADMIN"
                    })
                    continue

                if "WINLOCK_UNINSTALLED" in line:
                    events.append({
                        "time": ts,
                        "type": "WINLOCK",
                        "category": "system",
                        "badge": "Gỡ bảo vệ",
                        "badge_color": "slate",
                        "icon": "fa-solid fa-lock-open",
                        "title": "Gỡ bỏ bảo vệ màn hình khóa",
                        "details": "Đã gỡ bỏ FaceID và khôi phục màn hình khóa mặc định của hệ điều hành",
                        "user": "ADMIN"
                    })
                    continue

                if "PASSWORD_UPDATED" in line:
                    events.append({
                        "time": ts,
                        "type": "SECURITY",
                        "category": "system",
                        "badge": "Đổi mật mã",
                        "badge_color": "sky",
                        "icon": "fa-solid fa-key",
                        "title": "Cập nhật mật mã dự phòng",
                        "details": "Mật mã dự phòng (PIN/Password) mở máy đã được cập nhật thành công",
                        "user": "ADMIN"
                    })
                    continue

                if "POLICY_CHANGED" in line:
                    m_pol = re.search(r"POLICY_CHANGED:\s*(.*)", line)
                    msg_pol = m_pol.group(1) if m_pol else "Thay đổi chính sách bảo mật"
                    events.append({
                        "time": ts,
                        "type": "SECURITY",
                        "category": "system",
                        "badge": "Chính sách",
                        "badge_color": "sky",
                        "icon": "fa-solid fa-sliders",
                        "title": "Cấu hình bảo mật hệ thống",
                        "details": msg_pol,
                        "user": "ADMIN"
                    })
                    continue

        # Tra ve danh sach su kien dao nguoc (moi nhat len dau), gioi han toi da 100 su kien
        return events[::-1][:100]

    def get_logs(self):
        if sys.platform != "win32":
            try:
                r = subprocess.run(
                    ["journalctl", "-u", "faceid-auth.service", "-n", "60", "--no-pager"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout
            except Exception:
                pass
        for lf in (LOG_FILE, os.path.join(BASE_DIR, "data", "daemon.log")):
            if os.path.exists(lf):
                try:
                    with open(lf, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    if lines:
                        return "".join(lines[-60:])
                except Exception:
                    pass
        return "Không có dữ liệu nhật ký."

    def clear_all_logs(self):
        """Xoa sach file nhat ky he thong."""
        cleared = False
        for lf in (LOG_FILE, os.path.join(BASE_DIR, "data", "daemon.log"), os.path.join(BASE_DIR, "data", "daemon_err.log")):
            if os.path.exists(lf):
                try:
                    with open(lf, "w", encoding="utf-8") as f:
                        f.write("")
                    cleared = True
                except Exception:
                    pass
        return {"ok": True}

    def start_cam(self):
        self._drill_fast_start_time = time.time()
        if self._cam_running:
            return
        self._cam_running = True
        self._cam_thread = threading.Thread(target=self._cam_worker_loop, daemon=True)
        self._cam_thread.start()

    def stop_cam(self):
        self._cam_running = False

    def _cam_worker_loop(self):
        cam_idx = 0
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cam_idx = int(json.load(f).get("camera", {}).get("device_index", 0))
            except Exception:
                pass

        detector = YuNetDetector()
        fiqa = FaceQualityAssessor()
        anti_spoof = MiniFASNetDetector(real_threshold=PAD_REAL_THRESHOLD)
        embedder = ArcFaceEmbedder()
        fusion = MultiModalFusionEngine(sim_threshold=DEFAULT_SIM_THRESHOLD, decision_threshold=DEFAULT_DECISION_THRESHOLD)
        challenge = ChallengeResponseDetector(timeout_sec=DEFAULT_CHALLENGE_TIMEOUT_SEC, preload_hands=True)
        self._challenge_detector = challenge
        self.reset_challenge()
        all_templates = load_all_enrolled_templates()
        _tpl_state = {"t": time.time(), "sim_th": DEFAULT_SIM_THRESHOLD, "dec_th": DEFAULT_DECISION_THRESHOLD}
        def _refresh_runtime_policy():
            try:
                if os.path.exists(POLICY_FILE):
                    with open(POLICY_FILE, "r", encoding="utf-8") as pf:
                        pol_c = json.load(pf).get("contextual_policies", {})
                    lg = pol_c.get("windows-logon", pol_c.get("default", {}))
                    _tpl_state["sim_th"] = float(lg.get("sim_threshold", DEFAULT_SIM_THRESHOLD))
                    _tpl_state["dec_th"] = float(lg.get("decision_threshold", DEFAULT_DECISION_THRESHOLD))
            except Exception:
                pass
        _refresh_runtime_policy()
        cam_backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2
        cap = cv2.VideoCapture(cam_idx, cam_backend)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(cam_idx)

        if not cap.isOpened():
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        # Bat che do Auto Exposure de camera khong bi toi den
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0)
        except Exception:
            pass

        last_time = time.time()
        fps = 0.0

        try:
            while self._cam_running:
                if self._cam_view_mode in ("interactive", "drill_fast") and self._freeze_interactive:
                    time.sleep(0.05)
                    continue
                ret, raw_frame = cap.read()
                if time.time() - _tpl_state["t"] > 3.0:
                    _tpl_state["t"] = time.time()
                    try:
                        all_templates[:] = load_all_enrolled_templates()
                        _refresh_runtime_policy()
                    except Exception:
                        pass
                if not ret or raw_frame is None:
                    time.sleep(0.03)
                    continue

                now = time.time()
                dt = now - last_time
                last_time = now
                if dt > 0:
                    fps = 0.9 * fps + 0.1 * (1.0 / dt)

                metrics = {
                    "fps": f"{fps:.1f}",
                    "face_detected": False,
                    "fiqa_passed": False,
                    "is_live_fft": True,
                    "pad_score": 0.0,
                    "cosine_sim": 0.0,
                    "fused_conf": 0.0,
                    "decision": "NO_FACE",
                    "challenge_hint": self._challenge_hint,
                    "active_success": self._active_success,
                    "target_user": self._target_user
                }

                if self._cam_view_mode == "telemetry":
                    if not self._ai_busy:
                        self._ai_busy = True
                        f_copy = raw_frame.copy()
                        def _run_tel_ai(img_bgr, cur_fps):
                            try:
                                m = {
                                    "fps": round(cur_fps, 1), "face_detected": False, "fiqa_passed": False,
                                    "is_live_fft": True, "pad_score": 0.0, "cosine_sim": 0.0, "fused_conf": 0.0,
                                    "decision": "NO_FACE", "challenge_hint": "", "active_success": False, "target_user": "ALL", "matched_user": "", "yaw": 0.0, "pitch": 0.0
                                }
                                dets = [d for d in detector.detect(img_bgr) if (d[0][2] - d[0][0]) >= MIN_FACE_SIZE_PX and (d[0][1] - d[0][3]) >= MIN_FACE_SIZE_PX]
                                b_draw, l_draw, u_name = None, None, None
                                if len(dets) >= 1:
                                    dets.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                                    b, lm, _ = dets[0]
                                    b_draw, l_draw = b, lm
                                    m["face_detected"] = True
                                    q = fiqa.evaluate_quality(img_bgr, b, lm)
                                    m["fiqa_passed"] = q.passed
                                    m["is_live_fft"] = q.is_live_fft
                                    m["yaw"] = round(float(q.yaw), 1)
                                    m["pitch"] = round(float(q.pitch), 1)
                                    _, p_score, _ = anti_spoof.predict_smoothed(img_bgr, b)
                                    m["pad_score"] = float(p_score)
                                    if all_templates:
                                        emb = embedder.extract(img_bgr, b, lm)
                                        best_c, hi_s = None, 0.0
                                        for un, tl in all_templates:
                                            sims_with_t = [(float(1.0 - embedder.compute_distance(emb, t)), t) for t in tl]
                                            sims_with_t.sort(key=lambda x: x[0], reverse=True)
                                            ss = [x[0] for x in sims_with_t]
                                            es = (TOP1_TEMPLATE_WEIGHT * ss[0] + TOP2_TEMPLATE_WEIGHT * ss[1]) if len(ss) >= 2 else ss[0]
                                            if es > hi_s:
                                                hi_s, best_c = es, (un, sims_with_t[0][1])
                                        m["cosine_sim"] = float(hi_s)
                                        if best_c:
                                            eff_p = p_score if q.is_live_fft else (p_score * MOIRE_PASSIVE_PENALTY)
                                            fw_q = (1.0 if q.passed else LOW_FIQA_PENALTY) * (1.0 if q.is_live_fft else MOIRE_FIQA_PENALTY)
                                            cur_sim_th = _tpl_state.get("sim_th", DEFAULT_SIM_THRESHOLD)
                                            cur_dec_th = _tpl_state.get("dec_th", DEFAULT_DECISION_THRESHOLD)
                                            dec = fusion.evaluate_fused(
                                                passive_score=eff_p,
                                                active_passed=True,
                                                probe_emb=emb,
                                                template_emb=best_c[1],
                                                raw_cosine_sim=hi_s,
                                                best_user=best_c[0],
                                                fiqa_score=fw_q,
                                                sim_threshold=cur_sim_th,
                                                decision_threshold=cur_dec_th
                                            )
                                            m["fused_conf"] = float(dec.confidence_score)
                                            if dec.success and dec.confidence_score >= cur_dec_th:
                                                m["decision"] = "GENUINE"
                                                u_name = best_c[0]
                                                m["matched_user"] = u_name
                                            else:
                                                m["decision"] = "REJECTED"
                                lbl = u_name if (m["decision"] == "GENUINE" and u_name) else ""
                                col = (46, 204, 113) if m["decision"] == "GENUINE" else ((70, 70, 245) if m["decision"] == "REJECTED" else (255, 170, 0))
                                self._ai_cache = {"box": b_draw, "landmarks": l_draw, "metrics": m, "label": lbl, "color": col}
                            except Exception:
                                pass
                            finally:
                                self._ai_busy = False
                        threading.Thread(target=_run_tel_ai, args=(f_copy, fps), daemon=True).start()

                    # Vẽ nhanh từ cache và đẩy MJPEG ngay lập tức (25-30 FPS)
                    c = self._ai_cache
                    draw_frame = cv2.flip(raw_frame, 1)
                    fh, fw = draw_frame.shape[:2]
                    if c["box"] is not None:
                        top, right, bottom, left = [int(v) for v in c["box"]]
                        bx1, bx2 = max(0, fw - right), min(fw - 1, fw - left)
                        by1, by2 = max(0, top), min(fh - 1, bottom)
                        cv2.rectangle(draw_frame, (bx1, by1), (bx2, by2), c["color"], 2)
                        if c["landmarks"] is not None:
                            for pt in c["landmarks"]:
                                cv2.circle(draw_frame, (int(fw - pt[0]), int(pt[1])), 4, (0, 255, 255), -1)
                        if c.get("label"):
                            name_txt = str(c["label"])
                            x, y = bx1, max(24, by1 - 8)
                            cv2.putText(draw_frame, name_txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 3, cv2.LINE_AA)
                            cv2.putText(draw_frame, name_txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (46, 204, 113), 2, cv2.LINE_AA)
                    h, w = draw_frame.shape[:2]
                    small = cv2.resize(draw_frame, (640, int(h * (640 / float(w)))))
                    ok_enc, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok_enc:
                        self._latest_jpeg = buf.tobytes()
                        self._latest_frame_id += 1
                    now_ui = time.time()
                    if self._window and c["metrics"] and (now_ui - getattr(self, "_last_ui_ts", 0.0)) >= 0.15:
                        self._last_ui_ts = now_ui
                        m_out = dict(c["metrics"])
                        m_out["fps"] = round(fps, 1)
                        try:
                            self._window.evaluate_js(f"updateCamFrame(null, {json.dumps(m_out)});")
                        except Exception:
                            pass
                    time.sleep(0.01)
                    continue

                # Luồng AI nền cho chế độ 2 (drill_fast) và 3 (interactive)
                if not self._ai_busy and not self._freeze_interactive:
                    self._ai_busy = True
                    f_copy = raw_frame.copy()
                    def _run_drill_ai(img_bgr, cur_fps):
                        try:
                            m = {
                                "fps": round(cur_fps, 1),
                                "face_detected": False,
                                "fiqa_passed": False,
                                "is_live_fft": True,
                                "pad_score": 0.0,
                                "cosine_sim": 0.0,
                                "fused_conf": 0.0,
                                "decision": "NO_FACE",
                                "challenge_hint": self._challenge_hint,
                                "active_success": self._active_success,
                                "target_user": "ALL",
                                "matched_user": "",
                                "yaw": 0.0,
                                "pitch": 0.0
                            }
                            dets = [d for d in detector.detect(img_bgr) if (d[0][2] - d[0][0]) >= MIN_FACE_SIZE_PX and (d[0][1] - d[0][3]) >= MIN_FACE_SIZE_PX]
                            b_draw, l_draw, u_name = None, None, None
                            if len(dets) >= 1:
                                dets.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                                b, lm, _ = dets[0]
                                b_draw, l_draw = b, lm
                                m["face_detected"] = True
                                q = fiqa.evaluate_quality(img_bgr, b, lm)
                                m["fiqa_passed"] = q.passed
                                m["is_live_fft"] = q.is_live_fft
                                m["yaw"] = round(float(q.yaw), 1)
                                m["pitch"] = round(float(q.pitch), 1)
                                pose = (q.yaw, q.pitch, q.roll)

                                _, pad_score, _ = anti_spoof.predict_smoothed(img_bgr, b)
                                m["pad_score"] = float(pad_score)

                                just_passed_active = False
                                if self._cam_view_mode == "interactive":
                                    if not self._active_success:
                                        passed, hint, _ = challenge.check_action(img_bgr, pose, lm, b)
                                        self._challenge_hint = _localize_hint(hint)
                                        m["challenge_hint"] = self._challenge_hint
                                        if passed:
                                            just_passed_active = True
                                            self._active_success = True
                                            self._active_success_time = time.time()
                                            self._frontal_streak = 0
                                            self._challenge_hint = "Đã xác thực cử động. Vui lòng nhìn thẳng vào camera..."
                                            m["challenge_hint"] = self._challenge_hint
                                    else:
                                        if (time.time() - self._active_success_time) > DEFAULT_VERIFY_TIMEOUT_SEC and not self._authenticated:
                                            self.reset_challenge()
                                            self._challenge_hint = "Hết thời gian phiên xác thực! Vui lòng thử lại."
                                            m["challenge_hint"] = self._challenge_hint

                                m["active_success"] = self._active_success

                                fast_ready = True
                                if self._cam_view_mode == "drill_fast":
                                    elapsed_fast = time.time() - getattr(self, "_drill_fast_start_time", 0.0)
                                    if elapsed_fast < DRILL_FAST_COUNTDOWN_SEC:
                                        fast_ready = False
                                        rem = int(DRILL_FAST_COUNTDOWN_SEC - elapsed_fast) + 1
                                        m["challenge_hint"] = f"Giữ thẳng khuôn mặt ({rem}s)..."

                                is_strict_frontal = q.passed and abs(q.yaw) <= STRICT_FRONTAL_MAX_YAW and abs(q.pitch) <= STRICT_FRONTAL_MAX_PITCH
                                if self._cam_view_mode == "interactive":
                                    # Neu la thu thach xoay/ngang/cui dau, bat buoc mat phai tro ve gan goc xuat phat ban dau
                                    pose_returned = True
                                    if challenge._pose_base is not None and challenge.current_action in (
                                        ActionType.LOOK_UP, ActionType.NOD_DOWN, ActionType.TURN_LEFT, ActionType.TURN_RIGHT
                                    ):
                                        byaw, bpitch = challenge._pose_base
                                        cur_y, cur_p = getattr(challenge, "_pose_ema", None) or (q.yaw, q.pitch)
                                        cur_y = 0.60 * q.yaw + 0.40 * cur_y
                                        cur_p = 0.60 * q.pitch + 0.40 * cur_p
                                        challenge._pose_ema = (cur_y, cur_p)
                                        if abs(cur_y - byaw) > POSE_RETURN_MAX_DYAW or abs(cur_p - bpitch) > POSE_RETURN_MAX_DPITCH:
                                            pose_returned = False

                                    # Sau khi vuot qua thu thach, bat buoc khuon mat phai quay ve nhin thang on dinh
                                    if self._active_success and not just_passed_active and is_strict_frontal and pose_returned:
                                        self._frontal_streak += 1
                                    else:
                                        self._frontal_streak = 0
                                    is_frontal = (self._frontal_streak >= FRONTAL_STREAK_REQUIRED)
                                else:
                                    is_frontal = is_strict_frontal
                                    if fast_ready and not is_frontal:
                                        m["challenge_hint"] = "Vui lòng nhìn thẳng vào camera..."

                                can_match = (self._cam_view_mode == "drill_fast" and fast_ready and is_frontal) or (self._cam_view_mode == "interactive" and self._active_success and is_frontal)
                                if self._cam_view_mode == "interactive" and self._active_success and not is_frontal:
                                    m["decision"] = "WAITING_ACTION"
                                    m["challenge_hint"] = "Đã xác thực cử động. Vui lòng nhìn thẳng vào camera..."

                                if can_match and all_templates:
                                    emb = embedder.extract(img_bgr, b, lm)
                                    best_cand, highest_sim = None, 0.0
                                    best_tpl = None
                                    for uname, t_list in all_templates:
                                        # Tim template co do tuong dong cao nhat trong danh sach mau cua user do
                                        sims_with_tpl = [(float(1.0 - embedder.compute_distance(emb, t)), t) for t in t_list]
                                        sims_with_tpl.sort(key=lambda x: x[0], reverse=True)
                                        sims = [x[0] for x in sims_with_tpl]
                                        eff_sim = (TOP1_TEMPLATE_WEIGHT * sims[0] + TOP2_TEMPLATE_WEIGHT * sims[1]) if len(sims) >= 2 else sims[0]
                                        if eff_sim > highest_sim:
                                            highest_sim = eff_sim
                                            best_cand = (uname, sims_with_tpl[0][1])
                                    m["cosine_sim"] = float(highest_sim)
                                    if best_cand:
                                        eff_passive = pad_score if q.is_live_fft else (pad_score * MOIRE_PASSIVE_PENALTY)
                                        fiqa_w = (1.0 if q.passed else LOW_FIQA_PENALTY) * (1.0 if q.is_live_fft else MOIRE_FIQA_PENALTY)
                                        cur_sim_th = _tpl_state.get("sim_th", DEFAULT_SIM_THRESHOLD)
                                        cur_dec_th = _tpl_state.get("dec_th", DEFAULT_DECISION_THRESHOLD)
                                        dec = fusion.evaluate_fused(
                                            passive_score=eff_passive,
                                            active_passed=True,
                                            probe_emb=emb,
                                            template_emb=best_cand[1],
                                            raw_cosine_sim=highest_sim,
                                            best_user=best_cand[0],
                                            fiqa_score=fiqa_w,
                                            sim_threshold=cur_sim_th,
                                            decision_threshold=cur_dec_th
                                        )
                                        m["fused_conf"] = float(dec.confidence_score)
                                        if dec.success and dec.confidence_score >= cur_dec_th:
                                            m["decision"] = "GENUINE"
                                            u_name = best_cand[0]
                                            m["matched_user"] = u_name
                                            self._authenticated = True
                                            self._auth_conf = dec.confidence_score
                                            self._challenge_hint = f"Nhận diện thành công: {u_name} ({int(dec.confidence_score*100)}%)"
                                            m["challenge_hint"] = self._challenge_hint
                                            m["active_success"] = True
                                            self._freeze_interactive = True
                                        else:
                                            # Khong khoa (freeze) ngay tuc khac! Cho phep camera tiep tuc quet trong 4 giay
                                            now_t = time.time()
                                            drill_expired = False
                                            if self._cam_view_mode == "drill_fast":
                                                # Bat dau tinh gio quet tu khi fast_ready = True
                                                scan_start = getattr(self, "_drill_scan_start_time", 0.0)
                                                if scan_start == 0.0:
                                                    self._drill_scan_start_time = now_t
                                                elif (now_t - scan_start) > DRILL_FAST_SCAN_WINDOW_SEC:
                                                    drill_expired = True
                                            elif self._cam_view_mode == "interactive":
                                                if (now_t - self._active_success_time) > INTERACTIVE_SCAN_WINDOW_SEC:
                                                    drill_expired = True

                                            if drill_expired:
                                                m["decision"] = "REJECTED"
                                                self._challenge_hint = "Không trùng khớp danh tính người dùng!"
                                                m["challenge_hint"] = self._challenge_hint
                                                self._freeze_interactive = True
                                            else:
                                                m["decision"] = "WAITING_ACTION"
                                                m["challenge_hint"] = f"Đang quét nhận diện... ({int(highest_sim*100)}%)"
                                elif self._cam_view_mode == "interactive" and not self._active_success:
                                    m["decision"] = "WAITING_ACTION"
                            else:
                                self._frontal_streak = 0
                                m["challenge_hint"] = "Vui lòng đưa khuôn mặt vào khung hình..."

                            lbl = u_name if (m["decision"] == "GENUINE" and u_name) else ""
                            col = (46, 204, 113) if m["decision"] == "GENUINE" else ((70, 70, 245) if m["decision"] == "REJECTED" else (255, 170, 0))
                            self._ai_cache = {"box": b_draw, "landmarks": l_draw, "metrics": m, "label": lbl, "color": col}

                            # Nếu vừa chốt đóng băng -> ghi lại đúng frame khoảnh khắc đó và đẩy UI ngay
                            if self._freeze_interactive:
                                df = cv2.flip(img_bgr, 1)
                                fh2, fw2 = df.shape[:2]
                                if b_draw is not None:
                                    t2, r2, bt2, l2 = [int(v) for v in b_draw]
                                    bx1, bx2 = max(0, fw2 - r2), min(fw2 - 1, fw2 - l2)
                                    by1, by2 = max(0, t2), min(fh2 - 1, bt2)
                                    cv2.rectangle(df, (bx1, by1), (bx2, by2), col, 2)
                                    if l_draw is not None:
                                        for pt in l_draw:
                                            cv2.circle(df, (int(fw2 - pt[0]), int(pt[1])), 4, (0, 255, 255), -1)
                                    if lbl:
                                        name_txt = str(lbl)
                                        x2, y2 = bx1, max(24, by1 - 8)
                                        cv2.putText(df, name_txt, (x2, y2), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 3, cv2.LINE_AA)
                                        cv2.putText(df, name_txt, (x2, y2), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (46, 204, 113), 2, cv2.LINE_AA)
                                h2, w2 = df.shape[:2]
                                sm2 = cv2.resize(df, (640, int(h2 * (640 / float(w2)))))
                                ok2, buf2 = cv2.imencode(".jpg", sm2, [cv2.IMWRITE_JPEG_QUALITY, 80])
                                if ok2:
                                    self._latest_jpeg = buf2.tobytes()
                                    self._latest_frame_id += 1
                                if self._window:
                                    try:
                                        self._window.evaluate_js(f"updateCamFrame(null, {json.dumps(m)});")
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        finally:
                            self._ai_busy = False
                    threading.Thread(target=_run_drill_ai, args=(f_copy, fps), daemon=True).start()

                if not self._freeze_interactive:
                    c = self._ai_cache
                    draw_frame = cv2.flip(raw_frame, 1)
                    fh, fw = draw_frame.shape[:2]
                    if c["box"] is not None:
                        top, right, bottom, left = [int(v) for v in c["box"]]
                        bx1, bx2 = max(0, fw - right), min(fw - 1, fw - left)
                        by1, by2 = max(0, top), min(fh - 1, bottom)
                        cv2.rectangle(draw_frame, (bx1, by1), (bx2, by2), c["color"], 2)
                        if c["landmarks"] is not None:
                            for pt in c["landmarks"]:
                                cv2.circle(draw_frame, (int(fw - pt[0]), int(pt[1])), 4, (0, 255, 255), -1)
                        if c.get("label"):
                            name_txt = str(c["label"])
                            x, y = bx1, max(24, by1 - 8)
                            cv2.putText(draw_frame, name_txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 3, cv2.LINE_AA)
                            cv2.putText(draw_frame, name_txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (46, 204, 113), 2, cv2.LINE_AA)
                    h, w = draw_frame.shape[:2]
                    small = cv2.resize(draw_frame, (640, int(h * (640 / float(w)))))
                    ok_enc, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok_enc:
                        self._latest_jpeg = buf.tobytes()
                        self._latest_frame_id += 1
                    now_ui = time.time()
                    if self._window and c["metrics"] and (now_ui - getattr(self, "_last_ui_ts", 0.0)) >= 0.12:
                        self._last_ui_ts = now_ui
                        m_out = dict(c["metrics"])
                        m_out["fps"] = round(fps, 1)
                        try:
                            self._window.evaluate_js(f"updateCamFrame(null, {json.dumps(m_out)});")
                        except Exception:
                            pass
                time.sleep(0.01)
        finally:
            cap.release()


def main():
    api = FaceIDApi()
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")

    window = webview.create_window(
        title="VinFace Authentication - Trung Tâm Quản Trị Hệ Thống",
        url=html_path,
        js_api=api,
        width=1120,
        height=740,
        min_size=(960, 600),
        background_color="#f0f7ff"
    )
    api.set_window(window)
    webview.start(gui="edgechromium" if sys.platform == "win32" else None, debug=False)


if __name__ == "__main__":
    main()
