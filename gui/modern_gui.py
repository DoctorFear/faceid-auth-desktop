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
from core.liveness import ChallengeResponseDetector
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
    import re
    res = hint
    # Bỏ phần thông số kỹ thuật trong ngoặc như (dYaw=...), (dPitch=...), (EAR=...), [o_o]
    res = re.sub(r'\s*\([^)]*\)', '', res)
    res = re.sub(r'\s*\[[^\]]*\]', '', res)

    mapping = [
        ("QUAY TRAI", "Quay trái"),
        ("QUAY PHAI", "Quay phải"),
        ("NHIN THANG", "Nhìn thẳng"),
        ("Nhin thang", "Nhìn thẳng"),
        ("vao camera", "vào camera"),
        ("giu yen", "giữ yên"),
        ("chuyen sang", "chuyển sang"),
        ("Chuyen sang", "Chuyển sang"),
        ("hoan thanh", "hoàn thành"),
        ("Hoan thanh", "Hoàn thành"),
        ("mat", "mắt"),
        ("NHIN LEN", "Ngẩng lên"),
        ("NGUA LEN", "Ngẩng lên"),
        ("CUI XUONG", "Cúi xuống"),
        ("CHOP MAT 1 CAI", "Chớp mắt 1 cái"),
        ("CHOP MAT 2 CAI", "Chớp mắt 2 cái"),
        ("CHOP MAT", "Chớp mắt"),
        ("MO MIENG", "Mở miệng"),
        ("CUOI", "Cười"),
        ("Quay sang TRAI", "Quay sang trái"),
        ("Quay sang PHAI", "Quay sang phải"),
        ("Ngua mat LEN", "Ngẩng mặt lên"),
        ("Cui dau XUONG", "Cúi đầu xuống"),
        ("Vui long nhin thang", "Vui lòng nhìn thẳng"),
        ("Vui long chop mat", "Vui lòng chớp mắt"),
        ("Chop mat", "Chớp mắt"),
        ("Quay trai", "Quay trái"),
        ("Quay phai", "Quay phải"),
        ("Ngua len", "Ngẩng lên"),
        ("Cui xuong", "Cúi xuống"),
        ("Mo mieng", "Mở miệng"),
        ("Mieng", "Mở miệng"),
        ("Cuoi", "Cười"),
        ("Giu nguyen", "Giữ nguyên"),
        ("Giu yen", "Giữ yên"),
        ("Dang kiem tra", "Đang kiểm tra"),
        ("Da chop", "Đã chớp"),
        ("Da xong", "Đã xong"),
        ("Thanh cong", "Thành công"),
        ("That bai", "Thất bại"),
        ("Het thoi gian", "Hết thời gian"),
        ("khuon mat", "khuôn mặt"),
        ("Khuon mat", "Khuôn mặt"),
        ("Vui long", "Vui lòng"),
        ("buoc", "bước"),
        ("Buoc", "Bước"),
        ("lan!", "lần!"),
        ("cai", "cái"),
    ]
    for k, v in mapping:
        res = res.replace(k, v)
    return res.strip()

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
        self._active_success = False
        self._active_success_time = 0.0
        self._authenticated = False
        self._auth_conf = 0.0
        if self._cam_view_mode == "drill_fast":
            self._challenge_hint = "Nhìn thẳng vào camera để mở khóa nhanh..."
            return
        self._challenge_hint = "Đang chuẩn bị thử thách..."
        if self._challenge_detector:
            try:
                act = self._challenge_detector.reset_challenge()
                self._challenge_hint = _localize_hint(f"Vui lòng: {act.value}")
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
                    pol = json.load(f).get("contextual_policies", {})
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

    def save_settings(self, data):
        try:
            dec_thresh, sim_thresh = 0.68, 0.62
            lvl = int(data.get("security_level", 1))
            if lvl == 0:
                dec_thresh, sim_thresh = 0.64, 0.58
            elif lvl == 2:
                dec_thresh, sim_thresh = 0.74, 0.66

            tout = float(data.get("timeout_sec", 8))
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

                with open(POLICY_FILE, "w", encoding="utf-8") as f:
                    json.dump(pol, f, indent=2)

            cam_idx = int(data.get("camera_idx", 0))
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg.setdefault("camera", {})["device_index"] = cam_idx
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)

            # Chi cap nhat fallback_pw qua ham change_fallback_password rieng
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
                return True
            return False
        else:
            sh = os.path.abspath(os.path.join(BASE_DIR, "install_ubuntu_lock.sh"))
            if os.path.exists(sh):
                subprocess.Popen(["bash", sh], cwd=BASE_DIR, start_new_session=True)
                return True
            return False

    def uninstall_windows_lock(self):
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
        script = os.path.join(BASE_DIR, "scripts", "enroll.py")
        popen_kw = {"cwd": BASE_DIR}
        if sys.platform == "win32":
            popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen([sys.executable, script, "--user", name], **popen_kw)
        return True

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
        return deleted

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
        anti_spoof = MiniFASNetDetector(real_threshold=0.60)
        embedder = ArcFaceEmbedder()
        fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.68)
        challenge = ChallengeResponseDetector(timeout_sec=16.0, preload_hands=True)
        self._challenge_detector = challenge
        self.reset_challenge()
        all_templates = load_all_enrolled_templates()

        cam_backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2
        cap = cv2.VideoCapture(cam_idx, cam_backend)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(cam_idx)

        if not cap.isOpened():
            return

        last_time = time.time()
        fps = 0.0

        try:
            while self._cam_running:
                if self._cam_view_mode in ("interactive", "drill_fast") and self._freeze_interactive:
                    time.sleep(0.05)
                    continue
                ret, raw_frame = cap.read()
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

                # AI model (YuNet, FIQA, AntiSpoof, Embedder, Liveness) xu ly tren raw_frame goc (chua flip)
                # de dong bo chinh xac truc toa do trai/phai (Yaw) va vi tri mat
                detections = detector.detect(raw_frame)
                box_to_draw = None
                landmarks_to_draw = None
                matched_user_name = None

                # Lọc template theo target_user nếu đang chọn cụ thể 1 người
                active_templates = all_templates
                # Luôn đối chiếu với tất cả khuôn mặt đã lưu

                if len(detections) >= 1:
                    detections.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                    box, landmarks, _ = detections[0]
                    box_to_draw = box
                    landmarks_to_draw = landmarks
                    metrics["face_detected"] = True

                    q = fiqa.evaluate_quality(raw_frame, box, landmarks)
                    metrics["fiqa_passed"] = q.passed
                    metrics["is_live_fft"] = q.is_live_fft
                    pose = (q.yaw, q.pitch, q.roll)

                    if self._cam_view_mode != "interactive" or self._active_success or (int(time.time() * 10) % 3 == 0):
                        _, pad_score, _ = anti_spoof.predict_smoothed(raw_frame, box)
                        metrics["pad_score"] = float(pad_score)
                    else:
                        metrics["pad_score"] = float(getattr(anti_spoof, "last_score", 0.5))

                    # Ở chế độ Interactive (Active Liveness): Yêu cầu vượt qua thử thách
                    if self._cam_view_mode == "interactive":
                        if not self._active_success:
                            # Chạy kiểm tra hành động (chớp mắt, quay đầu, giơ tay...)
                            passed, hint, dbg = challenge.check_action(raw_frame, pose, landmarks, box)
                            self._challenge_hint = _localize_hint(hint)
                            metrics["challenge_hint"] = self._challenge_hint
                            if passed:
                                self._active_success = True
                                self._active_success_time = time.time()
                                self._challenge_hint = "LIVENESS THÀNH CÔNG! ĐANG ĐỐI CHIẾU MẶT..."
                        else:
                            # Đã pass liveness trong vòng 3.5 giây tiếp theo
                            elapsed = time.time() - self._active_success_time
                            if elapsed > 8.0 and not self._authenticated:
                                self.reset_challenge()
                                self._challenge_hint = "Hết thời gian phiên liveness! Vui lòng thử lại."

                    metrics["active_success"] = self._active_success

                    # Chỉ match khuôn mặt khi: Ở chế độ Telemetry HOẶC ở chế độ Interactive và đã pass active_success
                    fast_ready = True
                    if self._cam_view_mode == "drill_fast":
                        elapsed_fast = time.time() - getattr(self, "_drill_fast_start_time", 0.0)
                        if elapsed_fast < 3.0:
                            fast_ready = False
                            rem = int(3.0 - elapsed_fast) + 1
                            metrics["challenge_hint"] = f"Chuẩn bị nhận diện sau {rem} giây..."
                    is_frontal = q.passed
                    can_match = (self._cam_view_mode == "telemetry") or (self._cam_view_mode == "drill_fast" and fast_ready and is_frontal) or (self._cam_view_mode == "interactive" and self._active_success and is_frontal)
                    if self._cam_view_mode == "interactive" and self._active_success and not is_frontal:
                        metrics["decision"] = "WAITING_ACTION"
                        metrics["challenge_hint"] = "Đã đạt Liveness! Vui lòng nhìn thẳng vào camera..."

                    if can_match and active_templates:
                        emb = embedder.extract(raw_frame, box, landmarks)
                        best_cand = None
                        highest_sim = 0.0

                        for uname, t_list in active_templates:
                            sims = [float(1.0 - embedder.compute_distance(emb, t)) for t in t_list]
                            sims.sort(reverse=True)
                            eff_sim = (0.7 * sims[0] + 0.3 * sims[1]) if len(sims) >= 2 else sims[0]
                            if eff_sim > highest_sim:
                                highest_sim = eff_sim
                                best_cand = (uname, t_list[0])

                        metrics["cosine_sim"] = float(highest_sim)
                        if best_cand:
                            eff_passive = pad_score if q.is_live_fft else (pad_score * 0.82)
                            fiqa_w = (1.0 if q.passed else 0.4) * (1.0 if q.is_live_fft else 0.85)
                            dec = fusion.evaluate_fused(
                                passive_score=eff_passive,
                                active_passed=True,
                                probe_emb=emb,
                                template_emb=best_cand[1],
                                raw_cosine_sim=highest_sim,
                                best_user=best_cand[0],
                                fiqa_score=fiqa_w,
                                sim_threshold=0.62,
                                decision_threshold=0.68
                            )
                            metrics["fused_conf"] = float(dec.confidence_score)
                            if dec.success and dec.confidence_score >= 0.68:
                                metrics["decision"] = "GENUINE"
                                matched_user_name = best_cand[0]
                                self._authenticated = True
                                self._auth_conf = dec.confidence_score
                                self._challenge_hint = f"XÁC THỰC THÀNH CÔNG: {matched_user_name} ({int(dec.confidence_score*100)}%)"
                                if self._cam_view_mode in ("interactive", "drill_fast"):
                                    metrics["challenge_hint"] = self._challenge_hint
                                    metrics["active_success"] = True
                                    self._freeze_interactive = True
                            else:
                                metrics["decision"] = "REJECTED"
                                if self._cam_view_mode == "drill_fast":
                                    self._challenge_hint = "TỪ CHỐI: Khuôn mặt không khớp!"
                                    metrics["challenge_hint"] = self._challenge_hint
                                    self._freeze_interactive = True
                                elif self._cam_view_mode == "interactive" and self._active_success:
                                    # Giống server.py: không chốt TỪ CHỐI sớm, cứ thử tiếp cho đến khi hết 8s mới chốt
                                    if (time.time() - self._active_success_time) > 7.5:
                                        self._challenge_hint = "TỪ CHỐI: Khuôn mặt không khớp!"
                                        metrics["challenge_hint"] = self._challenge_hint
                                        self._freeze_interactive = True
                                    else:
                                        metrics["decision"] = "WAITING_ACTION"
                                        metrics["challenge_hint"] = "Đã đạt Liveness! Vui lòng nhìn thẳng vào camera..."
                    elif self._cam_view_mode == "interactive" and not self._active_success:
                        metrics["decision"] = "WAITING_ACTION"
                else:
                    if self._cam_view_mode in ("interactive", "drill_fast"):
                        metrics["challenge_hint"] = "Đưa khuôn mặt vào giữa khung hình camera..."

                # Sau khi AI phan tich tren raw_frame xong, tien hanh flip ngang de hien thi soi guong (mirror)
                draw_frame = cv2.flip(raw_frame, 1)
                fh, fw = draw_frame.shape[:2]
                # Bounding box & landmarks overlay
                if box_to_draw is not None:
                    # box tra ve tu YuNetDetector la (top, right, bottom, left)
                    top, right, bottom, left = [int(v) for v in box_to_draw]
                    # Khi frame duoc flip ngang, toa do ngang x tro thanh: fw - x
                    bx1 = max(0, fw - right)
                    bx2 = min(fw - 1, fw - left)
                    by1 = max(0, top)
                    by2 = min(fh - 1, bottom)

                    if metrics["decision"] == "GENUINE":
                        box_color = (46, 204, 113)  # Emerald green
                        label_str = f"MATCH: {matched_user_name} ({int(metrics['fused_conf']*100)}%)"
                    elif metrics["decision"] == "REJECTED":
                        box_color = (70, 70, 245)   # Red
                        label_str = f"REJECTED ({int(metrics['fused_conf']*100)}%)"
                    else:
                        box_color = (255, 170, 0)   # Sky cyan / yellow
                        label_str = "DETECTING..."

                    cv2.rectangle(draw_frame, (bx1, by1), (bx2, by2), box_color, 2)

                    # Ve 5 diem landmarks tren khuon mat
                    if landmarks_to_draw is not None:
                        for pt in landmarks_to_draw:
                            px = int(fw - pt[0])
                            py = int(pt[1])
                            cv2.circle(draw_frame, (px, py), 4, (0, 255, 255), -1)

                    # Label badge ben tren khuon mat
                    label_y = max(24, by1 - 8)
                    cv2.putText(draw_frame, label_str, (bx1, label_y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, box_color, 2, cv2.LINE_AA)

                # Encode JPEG Base64 (giu nguyen ti le goc frame camera, khong ep ve 16:9 de tranh meo/bop mat)
                h, w = draw_frame.shape[:2]
                target_w = 640
                target_h = int(h * (target_w / float(w)))
                small = cv2.resize(draw_frame, (target_w, target_h))
                _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
                b64 = base64.b64encode(buf).decode("utf-8")

                if self._window:
                    js_code = f"updateCamFrame('{b64}', {json.dumps(metrics)});"
                    self._window.evaluate_js(js_code)

                time.sleep(0.03)
        finally:
            cap.release()


def main():
    api = FaceIDApi()
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")

    window = webview.create_window(
        title="FaceID Hybrid - Modern Vibrant Dashboard",
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
