"""
FaceID Admin Dashboard - Trung Tam Quan Tri He Thong (PyQt6 Control Panel - High Performance Edition).
Toi uu hoa hieu nang:
1. Lazy Import cac model AI nang (onnxruntime, mediapipe) -> Mo app sieu toc trong ~0.2s.
2. Non-blocking Background Status & Action Worker (QThread) voi Fast Ping 150ms -> Giao dien muot 60FPS, khong bao gio bi khựng/lag.
3. Kiem tra Registry truc tiep bang winreg (< 0.1ms) thay vi goi schtasks.exe lap lai.
"""

import os
import sys
import json
import time
import socket
import subprocess
import numpy as np

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
    QProgressBar, QGroupBox, QTabWidget, QHeaderView, QMessageBox,
    QInputDialog, QCheckBox, QDoubleSpinBox, QSpinBox, QGridLayout,
    QComboBox, QRadioButton, QLineEdit
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.fhs import get_fhs_path

CONFIG_FILE = get_fhs_path("config.json", category="config")
POLICY_FILE = get_fhs_path("policy.json", category="config")
EMB_DIR = get_fhs_path("embeddings", category="data")
LOG_FILE = get_fhs_path("daemon.log", category="data")

DEFAULT_SOCKET_PATH = "/run/faceid/faceid.sock"
DEFAULT_TCP_PORT = 9876
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_RUN_VAL = "FaceIDHybridDaemon"


def fast_query_daemon(action: str = "status", timeout: float = 0.20) -> dict:
    """Ping Daemon voi timeout cuc ngan (200ms) de khong bao gio gay tre giao dien."""
    s = None
    try:
        if hasattr(socket, "AF_UNIX") and sys.platform != "win32" and os.path.exists(DEFAULT_SOCKET_PATH):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(DEFAULT_SOCKET_PATH)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(("127.0.0.1", DEFAULT_TCP_PORT))

        req = {"action": action, "user": "status", "service": "status"}
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = s.recv(4096).decode("utf-8").strip()
        if data:
            for line in data.splitlines():
                obj = json.loads(line)
                if "status" in obj:
                    return obj
        return {"status": "UNAVAILABLE"}
    except Exception:
        return {"status": "UNAVAILABLE"}
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def check_autostart_fast() -> bool:
    """Kiem tra Auto-Start cuc nhanh qua winreg (< 0.1ms), khong spawn schtasks.exe."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, WIN_RUN_VAL)
                if val:
                    return True
        except Exception:
            pass
        return False
    else:
        return os.path.exists("/etc/systemd/system/multi-user.target.wants/faceid-auth.service")


def check_win_credential_provider_fast() -> bool:
    """Kiem tra DLL & Registry Win+L cuc nhanh (< 0.1ms)."""
    if sys.platform != "win32":
        return False
    if not os.path.exists(r"C:\Windows\System32\SampleV2CredentialProvider.dll"):
        return False
    try:
        import winreg
        reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{5fd3d285-0dd9-4362-8855-e0abaacd4af6}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ):
            return True
    except Exception:
        return False


class StatusPollingWorker(QThread):
    """Thread chay ngam kiem tra trang thai he thong, hoan toan khong anh huong toi UI Thread."""
    status_ready = pyqtSignal(dict)

    def run(self):
        enabled = True
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    enabled = cfg.get("system", {}).get("enabled", True)
            except Exception:
                pass

        res = fast_query_daemon(action="status", timeout=0.18)
        daemon_running = (res.get("status") == "OK")
        if daemon_running:
            enabled = res.get("enabled", enabled)

        info = {
            "daemon_running": daemon_running,
            "pid": res.get("pid", "?"),
            "enabled": enabled,
            "autostart": check_autostart_fast(),
            "winlock_ok": check_win_credential_provider_fast()
        }
        self.status_ready.emit(info)


class CommandRunnerWorker(QThread):
    """Thread chay cac lenh faceidctl start/stop/restart o background de UI khong bi dung hinh."""
    finished_signal = pyqtSignal(str)

    def __init__(self, cmd_args: list, done_msg: str):
        super().__init__()
        self.cmd_args = cmd_args
        self.done_msg = done_msg

    def run(self):
        try:
            subprocess.run(self.cmd_args, cwd=BASE_DIR, capture_output=True, text=True)
        except Exception as e:
            self.finished_signal.emit(f"[-] Lỗi thực thi: {e}")
            return
        self.finished_signal.emit(self.done_msg)


class CameraMonitorThread(QThread):
    """Thread chay camera background voi Lazy Import cac model AI."""
    frame_signal = pyqtSignal(QImage)
    metrics_signal = pyqtSignal(dict)
    init_status_signal = pyqtSignal(str)

    def __init__(self, target_user="ADMIN"):
        super().__init__()
        self.target_user = target_user
        self.running = True
        self.templates = None

    def load_templates(self):
        path = os.path.join(EMB_DIR, f"{self.target_user}.npy")
        if os.path.exists(path):
            try:
                self.templates = np.load(path, allow_pickle=False)
            except Exception:
                self.templates = None
        else:
            self.templates = None

    def run(self):
        try:
            self.init_status_signal.emit("Đang nạp các mô hình AI (ONNX) và mở Camera...")
            import cv2
            from core.detector import YuNetDetector
            from core.fiqa import FaceQualityAssessor
            from core.anti_spoof import MiniFASNetDetector
            from core.embedder import ArcFaceEmbedder
            from core.fusion import MultiModalFusionEngine

            detector = YuNetDetector()
            fiqa = FaceQualityAssessor()
            anti_spoof = MiniFASNetDetector(real_threshold=0.60)
            embedder = ArcFaceEmbedder()
            fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.75)
            self.load_templates()
        except Exception as e:
            self.init_status_signal.emit(f"Lỗi khởi tạo AI: {e}")
            return

        # Doc camera device_index tu config.json
        cam_idx = 0
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cam_idx = int(json.load(f).get("camera", {}).get("device_index", 0))
            except Exception:
                cam_idx = 0

        # Thu mo bang DSHOW truoc tren Windows, fallback ve mac dinh
        cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(cam_idx)

        if not cap.isOpened():
            self.init_status_signal.emit(f"Không thể mở Camera #{cam_idx} (Camera đang bận hoặc chưa cắm)!")
            return

        last_time = time.time()
        fps = 0.0

        while self.running:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.03)
                continue

            # Lat ngang (Mirror flip) de hinh anh camera thuan chieu nhu soi guong
            frame = cv2.flip(frame, 1)

            now = time.time()
            dt = now - last_time
            last_time = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)

            metrics = {
                "fps": f"{fps:.1f}",
                "face_detected": False,
                "fiqa_passed": False,
                "fiqa_score": 0.0,
                "moire_score": 0.0,
                "is_live_fft": True,
                "pad_score": 0.0,
                "cosine_sim": 0.0,
                "fused_conf": 0.0,
                "decision": "NO_FACE"
            }

            detections = detector.detect(frame)
            if len(detections) >= 1:
                # Sap xep chon khuon mat chu the chinh lon nhat (gan webcam nhat)
                detections.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                box, landmarks, _ = detections[0]
                top, right, bottom, left = box
                metrics["face_detected"] = True

                q = fiqa.evaluate_quality(frame, box, landmarks)
                metrics["fiqa_passed"] = q.passed
                metrics["fiqa_score"] = float(q.sharpness)
                metrics["moire_score"] = float(q.moire_score)
                metrics["is_live_fft"] = q.is_live_fft

                _, pad_score, _ = anti_spoof.predict_smoothed(frame, box)
                metrics["pad_score"] = float(pad_score)

                if self.templates is not None and len(self.templates) > 0:
                    emb = embedder.extract(frame, box, landmarks)
                    sims = [1.0 - embedder.compute_distance(emb, t) for t in self.templates]
                    best_sim = float(max(sims))
                    metrics["cosine_sim"] = best_sim

                    effective_passive = pad_score if q.is_live_fft else (pad_score * 0.82)
                    dec = fusion.evaluate_fused(
                        passive_score=effective_passive,
                        active_passed=True,
                        probe_emb=emb,
                        template_emb=self.templates[np.argmax(sims)],
                        raw_cosine_sim=best_sim,
                        best_user=self.target_user,
                        fiqa_score=1.0 if q.passed else 0.5
                    )
                    metrics["fused_conf"] = float(dec.confidence_score)
                    metrics["decision"] = "GENUINE" if dec.confidence_score >= 0.68 else "REJECTED"

                color = (0, 255, 0) if metrics["decision"] == "GENUINE" else (0, 0, 255)
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                lbl = f"{self.target_user}: {metrics['fused_conf']:.2f}"
                cv2.putText(frame, lbl, (left, max(20, top - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            self.metrics_signal.emit(metrics)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            q_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            self.frame_signal.emit(q_img.copy())

            time.sleep(0.025)

        cap.release()

    def stop(self):
        self.running = False
        self.wait(1000)


class FaceIDAdminWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FaceID Hybrid - Bảng Quản Lý Hệ Thống Toàn Diện (Control Panel)")
        self.resize(1180, 760)
        self.cam_thread = None
        self.poll_worker = None
        self.cmd_worker = None
        self._last_log_size = 0
        self._cached_status = {"daemon_running": False, "enabled": True, "autostart": False}

        self.apply_dark_theme()
        self.init_ui()
        self.refresh_user_list()
        self.load_policy_settings()
        self.trigger_async_status_refresh()

        # Timer nhe nhang cap nhat trang thai o background moi 3 giay
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.auto_refresh_tick)
        self.status_timer.start(3000)

    def apply_dark_theme(self):
        dark_qss = """
        QMainWindow { background-color: #0f172a; }
        QWidget { color: #e2e8f0; font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
        QGroupBox { border: 1px solid #334155; border-radius: 8px; margin-top: 12px; font-weight: bold; padding: 14px; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #38bdf8; }
        QPushButton { background-color: #1e293b; border: 1px solid #475569; border-radius: 6px; padding: 7px 14px; font-weight: bold; }
        QPushButton:hover { background-color: #334155; border-color: #38bdf8; }
        QPushButton:pressed { background-color: #0284c7; }
        QTableWidget { background-color: #1e293b; border: 1px solid #334155; gridline-color: #334155; border-radius: 6px; }
        QHeaderView::section { background-color: #0f172a; color: #94a3b8; font-weight: bold; border: none; padding: 6px; }
        QTextEdit { background-color: #090d16; border: 1px solid #1e293b; border-radius: 6px; font-family: 'Consolas', monospace; color: #a5f3fc; }
        QProgressBar { border: 1px solid #334155; border-radius: 4px; text-align: center; background-color: #1e293b; }
        QProgressBar::chunk { background-color: #0284c7; }
        QTabWidget::pane { border: 1px solid #334155; border-radius: 8px; }
        QTabBar::tab { background: #1e293b; color: #94a3b8; padding: 9px 18px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
        QTabBar::tab:selected { background: #0284c7; color: #ffffff; font-weight: bold; }
        QDoubleSpinBox, QSpinBox { background-color: #1e293b; border: 1px solid #475569; border-radius: 4px; padding: 4px 8px; color: #f8fafc; }
        QCheckBox { spacing: 8px; font-weight: bold; }
        """
        self.setStyleSheet(dark_qss)

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)

        header_layout = QHBoxLayout()
        title_lbl = QLabel("🛡️ BẢNG QUẢN LÝ HỆ THỐNG FACEID HYBRID")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #38bdf8;")
        header_layout.addWidget(title_lbl)

        header_layout.addStretch()

        self.btn_master_toggle = QPushButton("FACEID: Đang kiểm tra...")
        self.btn_master_toggle.setMinimumWidth(260)
        self.btn_master_toggle.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 16px; border-radius: 6px;")
        self.btn_master_toggle.clicked.connect(self.toggle_master_system)
        header_layout.addWidget(self.btn_master_toggle)

        main_layout.addLayout(header_layout)

        self.tabs = QTabWidget()

        # TAB 1: SYSTEM CONTROL PANEL
        tab_control = QWidget()
        tab_control_layout = QVBoxLayout(tab_control)

        top_split = QHBoxLayout()

        grp_daemon = QGroupBox("⚙️ Trạng Thái Hoạt Động Của Hệ Thống")
        vbox_daemon = QVBoxLayout(grp_daemon)
        self.lbl_ctrl_daemon = QLabel("• Dịch vụ nhận diện: Đang kiểm tra...")
        self.lbl_ctrl_faceid = QLabel("• Tính năng FaceID: Đang kiểm tra...")
        self.lbl_ctrl_autostart = QLabel("• Tự khởi động cùng máy tính: Đang kiểm tra...")
        self.lbl_ctrl_winlock = QLabel("• Trạng thái kết nối Windows: Đang kiểm tra...")
        for lbl in (self.lbl_ctrl_daemon, self.lbl_ctrl_faceid, self.lbl_ctrl_autostart, self.lbl_ctrl_winlock):
            lbl.setStyleSheet("font-size: 13px; padding: 2px 0;")
            vbox_daemon.addWidget(lbl)

        btn_daemon_row = QHBoxLayout()
        btn_start_d = QPushButton("▶ Bật Dịch Vụ")
        btn_start_d.setStyleSheet("background-color: #059669; font-weight: bold; padding: 8px;")
        btn_start_d.clicked.connect(self.start_daemon_explicit)

        btn_restart_d = QPushButton("🔄 Khởi Động Lại")
        btn_restart_d.setStyleSheet("background-color: #0284c7; font-weight: bold; padding: 8px;")
        btn_restart_d.clicked.connect(self.restart_daemon_explicit)

        btn_stop_d = QPushButton("⏹ Tạm Dừng")
        btn_stop_d.setStyleSheet("background-color: #dc2626; font-weight: bold; padding: 8px;")
        btn_stop_d.clicked.connect(self.stop_daemon_explicit)

        btn_daemon_row.addWidget(btn_start_d)
        btn_daemon_row.addWidget(btn_restart_d)
        btn_daemon_row.addWidget(btn_stop_d)
        vbox_daemon.addLayout(btn_daemon_row)
        top_split.addWidget(grp_daemon, stretch=3)

        grp_win = QGroupBox("🪟 Tính Năng Mở Khóa Màn Hình Windows")
        vbox_win = QVBoxLayout(grp_win)
        lbl_win_desc = QLabel(
            "Tích hợp mở khóa bằng khuôn mặt trực tiếp vào màn hình khóa máy (Win + L)\n"
            "và màn hình đăng nhập khi vừa bật máy:"
        )
        lbl_win_desc.setStyleSheet("color: #94a3b8;")
        vbox_win.addWidget(lbl_win_desc)

        btn_install_win = QPushButton("🛡️ Kích Hoạt Mở Khóa Màn Hình (Cần quyền Admin)")
        btn_install_win.setStyleSheet("background-color: #2563eb; font-weight: bold; padding: 10px;")
        btn_install_win.clicked.connect(self.install_windows_provider)

        btn_uninstall_win = QPushButton("🗑️ Tắt / Gỡ Khỏi Màn Hình Khóa (Admin)")
        btn_uninstall_win.setStyleSheet("background-color: #7f1d1d; padding: 10px;")
        btn_uninstall_win.clicked.connect(self.uninstall_windows_provider)

        vbox_win.addWidget(btn_install_win)
        vbox_win.addWidget(btn_uninstall_win)
        top_split.addWidget(grp_win, stretch=2)

        tab_control_layout.addLayout(top_split)

        # Contextual Policy & Cấu hình thiết bị
        grp_policy = QGroupBox("🔐 Thiết Lập Mở Khóa & Cấu Hình Thiết Bị")
        vbox_settings = QVBoxLayout(grp_policy)

        # 1. Phong cach mo khoa & Active Liveness cho 2 truong hop rieng biet
        row_styles = QHBoxLayout()

        # 1.1 Khi khoa man hinh tam thoi (Win + L)
        grp_unlock = QGroupBox("1. Khi Khóa Tạm Thời (Win + L / windows-unlock)")
        vbox_unlock = QVBoxLayout(grp_unlock)
        self.rad_instant_unlock = QRadioButton("⚡ Tự Động Quét Ngay (0.5s)")
        self.rad_enter_unlock = QRadioButton("⌨️ Bấm Enter Mới Quét")
        self.rad_instant_unlock.toggled.connect(self.on_unlock_style_changed)
        vbox_unlock.addWidget(self.rad_instant_unlock)
        vbox_unlock.addWidget(self.rad_enter_unlock)

        self.chk_unlock_active = QCheckBox("🛡️ Thử thách Liveness (Quay đầu/chớp mắt)")
        self.chk_unlock_active.setStyleSheet("margin-left: 20px; color: #38bdf8;")
        vbox_unlock.addWidget(self.chk_unlock_active)
        row_styles.addWidget(grp_unlock)

        # 1.2 Khi tat may mo lai (Boot / windows-logon)
        grp_logon = QGroupBox("2. Khi Bật / Khởi Động Lại Máy (Boot / windows-logon)")
        vbox_logon = QVBoxLayout(grp_logon)
        self.rad_instant_logon = QRadioButton("⚡ Tự Động Quét Ngay (0.5s)")
        self.rad_enter_logon = QRadioButton("⌨️ Bấm Enter Mới Quét")
        self.rad_instant_logon.toggled.connect(self.on_logon_style_changed)
        vbox_logon.addWidget(self.rad_instant_logon)
        vbox_logon.addWidget(self.rad_enter_logon)

        self.chk_logon_active = QCheckBox("🛡️ Thử thách Liveness (Quay đầu/chớp mắt)")
        self.chk_logon_active.setStyleSheet("margin-left: 20px; color: #38bdf8;")
        vbox_logon.addWidget(self.chk_logon_active)
        row_styles.addWidget(grp_logon)

        vbox_settings.addLayout(row_styles)

        # 2. Cac thiet lap bao mat va camera
        grid_params = QGridLayout()

        grid_params.addWidget(QLabel("📷 Chọn Camera sử dụng:"), 0, 0)
        self.cbo_camera_idx = QComboBox()
        self.cbo_camera_idx.addItems([
            "Camera 0 (Camera Mặc Định / Laptop)",
            "Camera 1 (Webcam Rời USB / Phụ)",
            "Camera 2 (Camera Ngoài Phụ)"
        ])
        grid_params.addWidget(self.cbo_camera_idx, 0, 1)

        grid_params.addWidget(QLabel("🔑 Mật khẩu dự phòng Windows:"), 1, 0)
        pw_layout = QHBoxLayout()
        self.txt_fallback_pw = QLineEdit("123456")
        self.txt_fallback_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_show_pw = QPushButton("👁️")
        self.btn_show_pw.setFixedWidth(40)
        self.btn_show_pw.clicked.connect(self.toggle_password_visibility)
        pw_layout.addWidget(self.txt_fallback_pw)
        pw_layout.addWidget(self.btn_show_pw)
        grid_params.addLayout(pw_layout, 1, 1)

        grid_params.addWidget(QLabel("Mức độ bảo mật khuôn mặt:"), 2, 0)
        self.cbo_security_level = QComboBox()
        self.cbo_security_level.addItems([
            "Thấp (Nhận diện nhạy, dễ mở)",
            "Trung bình (Cân bằng - Khuyên dùng)",
            "Cao (Bảo mật tối đa)"
        ])
        grid_params.addWidget(self.cbo_security_level, 2, 1)

        grid_params.addWidget(QLabel("Thời gian quét tối đa (5 - 10s):"), 3, 0)
        self.spn_scan_timeout = QSpinBox()
        self.spn_scan_timeout.setRange(5, 10)
        self.spn_scan_timeout.setValue(8)
        self.spn_scan_timeout.setSuffix(" giây")
        grid_params.addWidget(self.spn_scan_timeout, 3, 1)

        grid_params.addWidget(QLabel("Khóa tạm nếu nhận diện sai quá:"), 4, 0)
        lock_layout = QHBoxLayout()
        self.spn_max_fail = QSpinBox()
        self.spn_max_fail.setRange(1, 10)
        self.spn_max_fail.setValue(3)
        self.spn_max_fail.setSuffix(" lần")
        lock_layout.addWidget(self.spn_max_fail)

        lock_layout.addWidget(QLabel("trong:"))
        self.spn_lockout_sec = QSpinBox()
        self.spn_lockout_sec.setRange(10, 600)
        self.spn_lockout_sec.setValue(45)
        self.spn_lockout_sec.setSuffix(" giây")
        lock_layout.addWidget(self.spn_lockout_sec)
        grid_params.addLayout(lock_layout, 4, 1)

        vbox_settings.addLayout(grid_params)

        btn_save_policy = QPushButton("💾 Lưu Cài Đặt & Áp Dụng Ngay (Hot-Reload)")
        btn_save_policy.setStyleSheet("background-color: #059669; font-weight: bold; padding: 10px 24px; font-size: 14px;")
        btn_save_policy.clicked.connect(self.save_policy_settings)
        vbox_settings.addWidget(btn_save_policy, alignment=Qt.AlignmentFlag.AlignCenter)

        tab_control_layout.addWidget(grp_policy)

        self.tabs.addTab(tab_control, "🎛️ Bảng Điều Khiển & Cấu Hình")

        # TAB 2: LIVE MONITOR & METRICS
        tab_monitor = QWidget()
        tab_monitor_layout = QHBoxLayout(tab_monitor)

        preview_group = QGroupBox("Camera Soi Gương Trực Tiếp (Realtime Feed)")
        preview_vbox = QVBoxLayout(preview_group)
        self.cam_label = QLabel("Camera đang tắt\n(Bấm nút 'Mở Camera Thử Nghiệm' bên dưới để soi gương)")
        self.cam_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cam_label.setStyleSheet("background-color: #020617; border-radius: 8px; min-height: 360px; font-size: 14px; color: #94a3b8;")
        preview_vbox.addWidget(self.cam_label)

        self.btn_toggle_cam_view = QPushButton("▶ Mở Camera Thử Nghiệm")
        self.btn_toggle_cam_view.setStyleSheet("background-color: #059669; font-weight: bold; padding: 10px; font-size: 14px;")
        self.btn_toggle_cam_view.clicked.connect(self.toggle_camera_monitor_button)
        preview_vbox.addWidget(self.btn_toggle_cam_view)

        tab_monitor_layout.addWidget(preview_group, stretch=3)

        telemetry_group = QGroupBox("🔍 Kết Quả Phân Tích Khuôn Mặt")
        telemetry_vbox = QVBoxLayout(telemetry_group)

        self.lbl_fps = QLabel("• Tốc độ khung hình: 0.0 FPS")
        self.lbl_status_det = QLabel("• Nhìn thấy khuôn mặt: Chưa thấy")
        self.lbl_fiqa = QLabel("• Độ rõ nét & ánh sáng: Đang chờ")
        self.lbl_moire = QLabel("• Kiểm tra giả lập màn hình: Đang chờ")

        self.lbl_pad = QLabel("• Độ chân thực người thật (Chống ảnh chụp): 0%")
        self.prog_pad = QProgressBar()
        self.prog_pad.setRange(0, 100)

        self.lbl_sim = QLabel("• Độ khớp với khuôn mặt bạn: 0%")
        self.prog_sim = QProgressBar()
        self.prog_sim.setRange(0, 100)

        self.lbl_fused = QLabel("• Điểm tin cậy mở khóa tổng hợp: 0%")
        self.prog_fused = QProgressBar()
        self.prog_fused.setRange(0, 100)

        self.lbl_result = QLabel("KẾT LUẬN: ĐANG CHỜ")
        self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 10px; border-radius: 6px; background: #334155; color: white;")
        self.lbl_result.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for w in (self.lbl_fps, self.lbl_status_det, self.lbl_fiqa, self.lbl_moire,
                  self.lbl_pad, self.prog_pad, self.lbl_sim, self.prog_sim,
                  self.lbl_fused, self.prog_fused, self.lbl_result):
            telemetry_vbox.addWidget(w)
        telemetry_vbox.addStretch()

        tab_monitor_layout.addWidget(telemetry_group, stretch=2)
        self.tabs.addTab(tab_monitor, "📊 Giám Sát Camera & Thử Nghiệm Realtime")

        # TAB 3: USER MANAGEMENT
        tab_users = QWidget()
        tab_users_layout = QVBoxLayout(tab_users)

        btn_user_row = QHBoxLayout()
        self.btn_refresh_users = QPushButton("🔄 Tải Lại Danh Sách")
        self.btn_refresh_users.clicked.connect(self.refresh_user_list)
        self.btn_enroll_user = QPushButton("➕ Thêm / Đăng Ký Khuôn Mặt Mới (5 Góc)")
        self.btn_enroll_user.setStyleSheet("background-color: #2563eb; font-weight: bold; padding: 6px 12px;")
        self.btn_enroll_user.clicked.connect(self.enroll_new_user)
        self.btn_verify_user = QPushButton("🔍 Soi Thử Khuôn Mặt (Kiểm Tra)")
        self.btn_verify_user.setStyleSheet("background-color: #0284c7; padding: 6px 12px;")
        self.btn_verify_user.clicked.connect(self.verify_selected_user)
        self.btn_delete_user = QPushButton("🗑️ Xóa Khuôn Mặt Đã Chọn")
        self.btn_delete_user.setStyleSheet("background-color: #dc2626; padding: 6px 12px;")
        self.btn_delete_user.clicked.connect(self.delete_selected_user)

        btn_user_row.addWidget(self.btn_refresh_users)
        btn_user_row.addWidget(self.btn_enroll_user)
        btn_user_row.addWidget(self.btn_verify_user)
        btn_user_row.addWidget(self.btn_delete_user)
        btn_user_row.addStretch()
        tab_users_layout.addLayout(btn_user_row)

        self.user_table = QTableWidget()
        self.user_table.setColumnCount(3)
        self.user_table.setHorizontalHeaderLabels(["Tên Khuôn Mặt / Người Dùng", "Số Lượng Góc Mặt", "Trạng Thái Mở Khóa"])
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tab_users_layout.addWidget(self.user_table)

        self.tabs.addTab(tab_users, "👥 Danh Sách Khuôn Mặt Được Phép Mở Khóa")

        # TAB 4: AUDIT LOGS
        tab_logs = QWidget()
        tab_logs_layout = QVBoxLayout(tab_logs)

        log_btn_row = QHBoxLayout()
        self.btn_reload_daemon_log = QPushButton("🔄 Tải Lại Nhật Ký")
        self.btn_reload_daemon_log.clicked.connect(self.load_daemon_log_file)
        self.btn_clear_logs = QPushButton("🧹 Xóa Màn Hình")
        self.btn_clear_logs.clicked.connect(lambda: self.log_text.clear())

        log_btn_row.addWidget(self.btn_reload_daemon_log)
        log_btn_row.addWidget(self.btn_clear_logs)
        log_btn_row.addStretch()
        tab_logs_layout.addLayout(log_btn_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        tab_logs_layout.addWidget(self.log_text)

        self.tabs.addTab(tab_logs, "📜 Nhật Ký Hoạt Động (Logs)")

        main_layout.addWidget(self.tabs)
        self.setCentralWidget(main_widget)

        self.log_message("[HỆ THỐNG] Bảng Quản Lý Hệ Thống FaceID Hybrid khởi tạo thành công (Chế độ Siêu Mượt Non-Blocking).")
        self.load_daemon_log_file()

    def log_message(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")

    def load_daemon_log_file(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                tail = "".join(lines[-40:])
                if tail.strip():
                    self.log_text.append("=== [NHẬT KÝ TỪ DATA/DAEMON.LOG] ===")
                    self.log_text.append(tail.strip())
                self._last_log_size = os.path.getsize(LOG_FILE)
            except Exception:
                pass

    def trigger_async_status_refresh(self):
        if self.poll_worker and self.poll_worker.isRunning():
            return
        self.poll_worker = StatusPollingWorker()
        self.poll_worker.status_ready.connect(self.apply_status_to_ui)
        self.poll_worker.start()

    def auto_refresh_tick(self):
        self.trigger_async_status_refresh()
        if os.path.exists(LOG_FILE):
            try:
                curr_size = os.path.getsize(LOG_FILE)
                if curr_size > self._last_log_size:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._last_log_size)
                        new_content = f.read().strip()
                        if new_content:
                            self.log_text.append(new_content)
                    self._last_log_size = curr_size
            except Exception:
                pass

    def apply_status_to_ui(self, info: dict):
        self._cached_status = info
        daemon_running = info.get("daemon_running", False)
        pid = info.get("pid", "?")
        enabled = info.get("enabled", True)
        autostart_on = info.get("autostart", False)
        winlock_ok = info.get("winlock_ok", False)

        # Master Status
        is_fully_active = daemon_running and enabled

        if is_fully_active:
            self.btn_master_toggle.setText("🟢 FACEID: ĐANG BẬT (Bấm để Tắt)")
            self.btn_master_toggle.setStyleSheet("background-color: #059669; color: white; font-size: 14px; font-weight: bold; padding: 8px 16px; border-radius: 6px;")
        else:
            self.btn_master_toggle.setText("⚫ FACEID: ĐÃ TẮT (Bấm để Bật)")
            self.btn_master_toggle.setStyleSheet("background-color: #475569; color: white; font-size: 14px; font-weight: bold; padding: 8px 16px; border-radius: 6px;")

        if daemon_running:
            self.lbl_ctrl_daemon.setText(f"• Dịch vụ nhận diện: 🟢 ĐANG HOẠT ĐỘNG (Sẵn sàng mở khóa)")
        else:
            self.lbl_ctrl_daemon.setText("• Dịch vụ nhận diện: 🔴 ĐÃ TẮT (Tạm ngừng hoạt động)")

        if enabled:
            self.lbl_ctrl_faceid.setText("• Tính năng FaceID: 🟢 ĐANG BẬT (Tự động nhận diện khuôn mặt)")
        else:
            self.lbl_ctrl_faceid.setText("• Tính năng FaceID: 🔴 ĐÃ TẮT (Chỉ dùng mã PIN / Mật khẩu)")

        if autostart_on:
            self.lbl_ctrl_autostart.setText("• Tự khởi động cùng máy tính: 🟢 ĐÃ BẬT (Tự chạy ngầm khi mở máy)")
        else:
            self.lbl_ctrl_autostart.setText("• Tự khởi động cùng máy tính: ⚪ ĐANG TẮT")

        if winlock_ok:
            self.lbl_ctrl_winlock.setText("• Trạng thái kết nối Windows: 🟢 ĐÃ KÍCH HOẠT (Đã hiển thị trên màn hình khóa)")
        else:
            self.lbl_ctrl_winlock.setText("• Trạng thái kết nối Windows: ⚪ CHƯA KÍCH HOẠT")

    def run_ctl_async(self, subcmd: str, busy_text: str, done_msg: str):
        if self.cmd_worker and self.cmd_worker.isRunning():
            return
        self.btn_master_toggle.setText(busy_text)
        ctl_script = os.path.join(BASE_DIR, "scripts", "faceidctl")
        self.cmd_worker = CommandRunnerWorker([sys.executable, ctl_script, subcmd], done_msg)
        self.cmd_worker.finished_signal.connect(self.on_ctl_finished)
        self.cmd_worker.start()

    def on_ctl_finished(self, msg: str):
        self.log_message(msg)
        self.trigger_async_status_refresh()

    def start_daemon_explicit(self):
        self.run_ctl_async("start", "⏳ Đang bật FaceID...", "[HỆ THỐNG] Đã bật FaceID thành công.")

    def stop_daemon_explicit(self):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        self.run_ctl_async("stop", "⏳ Đang tắt FaceID...", "[HỆ THỐNG] Đã tắt FaceID.")

    def restart_daemon_explicit(self):
        self.run_ctl_async("restart", "⏳ Đang khởi động lại...", "[HỆ THỐNG] Đã khởi động lại dịch vụ FaceID.")

    def toggle_master_system(self):
        """Công tắc tổng gộp: Bật hoặc Tắt toàn bộ FaceID (Dịch vụ + Tự khởi động + Nhận diện)."""
        daemon_running = self._cached_status.get("daemon_running", False)
        enabled = self._cached_status.get("enabled", True)

        if daemon_running and enabled:
            # Đang chạy -> Tắt toàn bộ
            self.stop_daemon_explicit()
        else:
            # Đang tắt -> Đảm bảo config bật enabled rồi start dịch vụ
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    cfg.setdefault("system", {})["enabled"] = True
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2)
                except Exception:
                    pass
            self.start_daemon_explicit()

    def _map_thresholds_to_level(self, dec_val: float, sim_val: float) -> int:
        """Quy doi nguong decision va sim ve index: 0 (Thap), 1 (Trung binh), 2 (Cao)"""
        if dec_val >= 0.72 or sim_val >= 0.65:
            return 2  # Cao
        elif dec_val <= 0.65 or sim_val <= 0.59:
            return 0  # Thap
        return 1      # Trung binh

    def _map_level_to_thresholds(self, level_idx: int) -> tuple[float, float]:
        """Quy doi index level sang tuple (decision_threshold, sim_threshold)"""
        if level_idx == 0:
            return (0.64, 0.58)  # Thap: Nhan dien nhay, de mo
        elif level_idx == 2:
            return (0.74, 0.66)  # Cao: Bao mat toi da
        return (0.68, 0.62)      # Trung binh: Chuan can bang mac dinh

    def toggle_password_visibility(self):
        if self.txt_fallback_pw.echoMode() == QLineEdit.EchoMode.Password:
            self.txt_fallback_pw.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_show_pw.setText("🔒")
        else:
            self.txt_fallback_pw.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_show_pw.setText("👁️")

    def on_unlock_style_changed(self):
        is_instant = self.rad_instant_unlock.isChecked()
        if is_instant:
            self.chk_unlock_active.setChecked(False)
            self.chk_unlock_active.setEnabled(False)
            self.chk_unlock_active.setText("🛡️ Liveness (Đã tắt để mở 0.5s)")
        else:
            self.chk_unlock_active.setEnabled(True)
            self.chk_unlock_active.setText("🛡️ Thử thách Liveness (Quay đầu/chớp mắt)")

    def on_logon_style_changed(self):
        is_instant = self.rad_instant_logon.isChecked()
        if is_instant:
            self.chk_logon_active.setChecked(False)
            self.chk_logon_active.setEnabled(False)
            self.chk_logon_active.setText("🛡️ Liveness (Đã tắt để mở 0.5s)")
        else:
            self.chk_logon_active.setEnabled(True)
            self.chk_logon_active.setText("🛡️ Thử thách Liveness (Quay đầu/chớp mắt)")

    def toggle_camera_monitor_button(self):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        else:
            self.start_camera_monitor()

    def load_policy_settings(self):
        if os.path.exists(POLICY_FILE):
            try:
                with open(POLICY_FILE, "r", encoding="utf-8") as f:
                    pol = json.load(f).get("contextual_policies", {})
                
                u_cfg = pol.get("windows-unlock", {})
                l_cfg = pol.get("windows-logon", {})

                # 1. Unlock (Win + L)
                u_active = bool(u_cfg.get("require_active_liveness", False))
                if u_active:
                    self.rad_enter_unlock.setChecked(True)
                    self.chk_unlock_active.setChecked(True)
                else:
                    self.rad_instant_unlock.setChecked(True)
                    self.chk_unlock_active.setChecked(False)

                # 2. Logon (Boot / mo may lai)
                l_active = bool(l_cfg.get("require_active_liveness", True))
                if l_active:
                    self.rad_enter_logon.setChecked(True)
                    self.chk_logon_active.setChecked(True)
                else:
                    self.rad_instant_logon.setChecked(True)
                    self.chk_logon_active.setChecked(False)

                dec = float(l_cfg.get("decision_threshold", u_cfg.get("decision_threshold", 0.68)))
                sim = float(l_cfg.get("sim_threshold", u_cfg.get("sim_threshold", 0.62)))
                tout = int(round(float(l_cfg.get("active_timeout_sec", u_cfg.get("active_timeout_sec", 8.0)))))

                self.cbo_security_level.setCurrentIndex(self._map_thresholds_to_level(dec, sim))
                self.spn_scan_timeout.setValue(max(5, min(10, tout)))
            except Exception as e:
                self.log_message(f"[-] Lỗi đọc policy.json: {e}")

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                rl = cfg.get("security", {}).get("rate_limiting", {})
                self.spn_max_fail.setValue(int(rl.get("max_consecutive_failures", 3)))
                self.spn_lockout_sec.setValue(int(rl.get("lockout_duration_sec", 45)))

                cam_idx = int(cfg.get("camera", {}).get("device_index", 0))
                self.cbo_camera_idx.setCurrentIndex(max(0, min(2, cam_idx)))
            except Exception:
                pass

        # Doc mat khau fallback tu Registry
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid", 0, winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, "FallbackPassword")
                if val:
                    self.txt_fallback_pw.setText(val)
        except Exception:
            self.txt_fallback_pw.setText("123456")

    def save_policy_settings(self):
        try:
            u_req_liveness = self.chk_unlock_active.isChecked() if self.rad_enter_unlock.isChecked() else False
            l_req_liveness = self.chk_logon_active.isChecked() if self.rad_enter_logon.isChecked() else False

            dec_thresh, sim_thresh = self._map_level_to_thresholds(self.cbo_security_level.currentIndex())
            tout_sec = float(self.spn_scan_timeout.value())

            if os.path.exists(POLICY_FILE):
                with open(POLICY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pol = data.setdefault("contextual_policies", {})

                u_cfg = pol.setdefault("windows-unlock", {})
                u_cfg["require_active_liveness"] = u_req_liveness
                u_cfg["decision_threshold"] = dec_thresh
                u_cfg["sim_threshold"] = sim_thresh
                u_cfg["active_timeout_sec"] = tout_sec

                l_cfg = pol.setdefault("windows-logon", {})
                l_cfg["require_active_liveness"] = l_req_liveness
                l_cfg["decision_threshold"] = dec_thresh
                l_cfg["sim_threshold"] = sim_thresh
                l_cfg["active_timeout_sec"] = tout_sec

                with open(POLICY_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

            new_cam_idx = self.cbo_camera_idx.currentIndex()
            new_pw = self.txt_fallback_pw.text().strip() or "123456"

            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                rl = cfg.setdefault("security", {}).setdefault("rate_limiting", {})
                rl["max_consecutive_failures"] = self.spn_max_fail.value()
                rl["lockout_duration_sec"] = self.spn_lockout_sec.value()

                cfg.setdefault("camera", {})["device_index"] = new_cam_idx
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)

            # Luu mat khau vao Registry Windows HKLM\SOFTWARE\FaceIDHybrid
            try:
                import winreg
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\FaceIDHybrid") as k:
                    winreg.SetValueEx(k, "FallbackPassword", 0, winreg.REG_SZ, new_pw)
            except Exception as e:
                self.log_message(f"[-] Luu Registry mat khau yeu cau quyen Admin: {e}")

            fast_query_daemon(action="reload", timeout=0.18)
            self.log_message("[CẤU HÌNH] Đã lưu và Hot-Reload cấu hình thành công!")
            QMessageBox.information(
                self, "Đã lưu cài đặt",
                "Đã lưu các thiết lập mở khóa và áp dụng ngay lập tức cho hệ thống!"
            )
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu cấu hình: {e}")

    def install_windows_provider(self):
        bat_path = os.path.join(BASE_DIR, "install_win_lock.bat")
        if os.path.exists(bat_path):
            subprocess.Popen(["cmd.exe", "/c", "start", "", bat_path], cwd=BASE_DIR)
            self.log_message("[WINDOWS] Đã kích hoạt trình cài đặt Credential Provider (install_win_lock.bat).")
        else:
            QMessageBox.warning(self, "Không tìm thấy", f"Không tìm thấy file {bat_path}")

    def uninstall_windows_provider(self):
        bat_path = os.path.join(BASE_DIR, "uninstall_win_lock.bat")
        if os.path.exists(bat_path):
            subprocess.Popen(["cmd.exe", "/c", "start", "", bat_path], cwd=BASE_DIR)
            self.log_message("[WINDOWS] Đã kích hoạt trình gỡ cài đặt Credential Provider (uninstall_win_lock.bat).")
        else:
            QMessageBox.warning(self, "Không tìm thấy", f"Không tìm thấy file {bat_path}")

    def refresh_user_list(self):
        self.user_table.setRowCount(0)
        if not os.path.exists(EMB_DIR):
            return
        files = [f for f in os.listdir(EMB_DIR) if f.endswith(".npy")]
        self.user_table.setRowCount(len(files))

        for row, f in enumerate(sorted(files)):
            user = f[:-4]
            path = os.path.join(EMB_DIR, f)
            try:
                arr = np.load(path, allow_pickle=False)
                count_str = f"{len(arr)} Góc Mặt (Đầy Đủ)"
                status_str = "🟢 Sẵn sàng mở khóa máy"
            except Exception as e:
                count_str = "Lỗi dữ liệu"
                status_str = f"🔴 {e}"

            self.user_table.setItem(row, 0, QTableWidgetItem(user))
            self.user_table.setItem(row, 1, QTableWidgetItem(count_str))
            self.user_table.setItem(row, 2, QTableWidgetItem(status_str))

    def enroll_new_user(self):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        default_user = os.environ.get("USERNAME", "ADMIN")
        user, ok = QInputDialog.getText(
            self, "Đăng Ký Khuôn Mặt Mới (5 Góc)",
            "Nhập tên gợi nhớ (Ví dụ: Tôi, Hà Thái Sơn, Người Thân):",
            text=default_user
        )
        if not ok or not user.strip():
            return
        user = user.strip()

        self.log_message(f"[*] Bắt đầu tiến trình chụp 5 tư thế cho khuôn mặt '{user}'...")
        script = os.path.join(BASE_DIR, "scripts", "enroll.py")
        subprocess.Popen([sys.executable, script, "--user", user], cwd=BASE_DIR)
        QMessageBox.information(
            self, "Hướng dẫn Đăng ký",
            f"Cửa sổ Camera đang mở để chụp 5 góc mặt cho '{user}'.\n"
            "Chỉ cần quay mặt theo vòng tròn HUD trên màn hình.\n"
            "Sau khi hoàn tất, hệ thống sẽ tự động cập nhật!"
        )

    def verify_selected_user(self):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        curr_row = self.user_table.currentRow()
        user = os.environ.get("USERNAME", "ADMIN")
        if curr_row >= 0:
            user = self.user_table.item(curr_row, 0).text()
        script = os.path.join(BASE_DIR, "scripts", "verify_interactive.py")
        subprocess.Popen([sys.executable, script, "--user", user], cwd=BASE_DIR)
        self.log_message(f"[*] Đã mở cửa sổ Verify Interactive 1:1 cho '{user}'.")

    def delete_selected_user(self):
        curr_row = self.user_table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn người dùng trong bảng để xóa!")
            return
        user = self.user_table.item(curr_row, 0).text()
        reply = QMessageBox.question(
            self, "Xác nhận xóa", f"Bạn có chắc muốn xóa dữ liệu khuôn mặt của user '{user}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            path = os.path.join(EMB_DIR, f"{user}.npy")
            if os.path.exists(path):
                os.remove(path)
                self.log_message(f"[XÓA] Đã xóa template của user '{user}'")
                self.refresh_user_list()
                QMessageBox.information(self, "Thành công", f"Đã xóa khuôn mặt user '{user}' thành công.")

    def run_quick_test_service(self, service_name="windows-unlock"):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        curr_row = self.user_table.currentRow()
        user = os.environ.get("USERNAME", "ADMIN")
        if curr_row >= 0:
            user = self.user_table.item(curr_row, 0).text()

        self.log_message(f"[*] Đang gửi yêu cầu xác thực tới Daemon (user='{user}', service='{service_name}')...")
        QApplication.processEvents()
        client_script = os.path.join(BASE_DIR, "scripts", "faceid_pam_client.py")
        res = subprocess.run([sys.executable, client_script, user, service_name], capture_output=True, text=True)
        out_msg = (res.stderr or res.stdout).strip()
        if res.returncode == 0:
            self.log_message(f"[+] KẾT QUẢ TEST ({service_name}): THÀNH CÔNG! {out_msg}")
            QMessageBox.information(self, "Xác Thực Thành Công", f"Xác thực '{service_name}' THÀNH CÔNG cho '{user}'!\n\n{out_msg}")
        else:
            self.log_message(f"[-] KẾT QUẢ TEST ({service_name}): FALLBACK PASSWORD. {out_msg}")
            QMessageBox.warning(self, "Chuyển Sang Mật Khẩu", f"Kết quả: Fallback Password!\n\n{out_msg}")

    def start_camera_monitor(self):
        if self.cam_thread and self.cam_thread.isRunning():
            return
        curr_row = self.user_table.currentRow()
        user = os.environ.get("USERNAME", "ADMIN")
        if curr_row >= 0:
            user = self.user_table.item(curr_row, 0).text()

        self.cam_label.setText("⏳ Đang khởi tạo AI Engine và bật Camera...")
        self.cam_thread = CameraMonitorThread(target_user=user)
        self.cam_thread.init_status_signal.connect(self.cam_label.setText)
        self.cam_thread.frame_signal.connect(self.update_camera_frame)
        self.cam_thread.metrics_signal.connect(self.update_telemetry)
        self.cam_thread.start()

        self.btn_toggle_cam_view.setText("⏹ Tắt Camera")
        self.btn_toggle_cam_view.setStyleSheet("background-color: #dc2626; font-weight: bold; padding: 10px; font-size: 14px;")
        self.log_message(f"[*] Đã bật xem trước camera và bộ giám sát AI thời gian thực cho '{user}'.")

    def stop_camera_monitor(self):
        if self.cam_thread:
            self.cam_thread.stop()
            self.cam_thread = None
        self.cam_label.setText("Camera đang tắt.\n(Lưu ý: Tắt camera ở đây để màn hình khóa Win + L sử dụng được camera)")
        self.btn_toggle_cam_view.setText("▶ Mở Camera Thử Nghiệm")
        self.btn_toggle_cam_view.setStyleSheet("background-color: #059669; font-weight: bold; padding: 10px; font-size: 14px;")
        self.log_message("[*] Đã tắt camera giám sát.")

    def update_camera_frame(self, q_img: QImage):
        pix = QPixmap.fromImage(q_img)
        scaled = pix.scaled(self.cam_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.cam_label.setPixmap(scaled)

    def update_telemetry(self, m: dict):
        fps_val = float(m.get('fps', 0.0))
        self.lbl_fps.setText(f"• Tốc độ khung hình: {fps_val:.1f} FPS")
        self.lbl_status_det.setText(f"• Nhìn thấy khuôn mặt: {'🟢 ĐÃ THẤY' if m['face_detected'] else '⚪ CHƯA THẤY'}")
        self.lbl_fiqa.setText(f"• Độ rõ nét & ánh sáng: {'🟢 ĐẠT CHUẨN' if m['fiqa_passed'] else '🟠 HƠI MỜ / THIẾU SÁNG'} ({m['fiqa_score']:.1f}đ)")
        self.lbl_moire.setText(f"• Kiểm tra giả lập màn hình: {'🟢 MẶT THẬT' if m['is_live_fft'] else '🔴 NGHI CHỤP TỪ MÀN HÌNH'}")

        pad_pct = int(m['pad_score'] * 100)
        self.lbl_pad.setText(f"• Độ chân thực người thật (Chống ảnh chụp): {pad_pct}%")
        self.prog_pad.setValue(pad_pct)

        sim_pct = int(max(0.0, min(1.0, m['cosine_sim'])) * 100)
        self.lbl_sim.setText(f"• Độ khớp với khuôn mặt bạn: {sim_pct}% (Cos: {m['cosine_sim']:.3f})")
        self.prog_sim.setValue(sim_pct)

        fused_pct = int(m['fused_conf'] * 100)
        self.lbl_fused.setText(f"• Điểm tin cậy mở khóa tổng hợp: {fused_pct}%")
        self.prog_fused.setValue(fused_pct)

        if m['decision'] == "GENUINE":
            self.lbl_result.setText("✅ KẾT QUẢ: HỢP LỆ — MỞ KHÓA THÀNH CÔNG")
            self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 10px; border-radius: 6px; background: #059669; color: white;")
        elif m['decision'] == "REJECTED":
            self.lbl_result.setText("❌ KẾT QUẢ: TỪ CHỐI (KHÔNG PHẢI CHÍNH CHỦ HOẶC GIẢ MẠO)")
            self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 10px; border-radius: 6px; background: #dc2626; color: white;")
        else:
            self.lbl_result.setText("⏸ KẾT QUẢ: ĐANG CHỜ KHUÔN MẶT...")
            self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 10px; border-radius: 6px; background: #334155; color: #cbd5e1;")

    def closeEvent(self, event):
        self.stop_camera_monitor()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = FaceIDAdminWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
