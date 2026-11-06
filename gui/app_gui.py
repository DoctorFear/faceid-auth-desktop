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
    QInputDialog, QCheckBox, QDoubleSpinBox, QSpinBox, QGridLayout
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

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.init_status_signal.emit("Không thể mở Camera (Camera đang bận hoặc chưa cắm)!")
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

        self.btn_toggle_autostart = QPushButton("Auto-Start: ...")
        self.btn_toggle_autostart.setMinimumWidth(185)
        self.btn_toggle_autostart.clicked.connect(self.toggle_autostart_state)
        header_layout.addWidget(self.btn_toggle_autostart)

        self.btn_toggle_daemon = QPushButton("Daemon: ...")
        self.btn_toggle_daemon.setMinimumWidth(195)
        self.btn_toggle_daemon.clicked.connect(self.toggle_daemon_process)
        header_layout.addWidget(self.btn_toggle_daemon)

        self.btn_toggle_faceid = QPushButton("FaceID: ...")
        self.btn_toggle_faceid.setMinimumWidth(165)
        self.btn_toggle_faceid.clicked.connect(self.toggle_faceid_state)
        header_layout.addWidget(self.btn_toggle_faceid)

        main_layout.addLayout(header_layout)

        self.tabs = QTabWidget()

        # TAB 1: SYSTEM CONTROL PANEL
        tab_control = QWidget()
        tab_control_layout = QVBoxLayout(tab_control)

        top_split = QHBoxLayout()

        grp_daemon = QGroupBox("⚙️ Điều Khiển Tiến Trình Ngầm (Daemon & Auto-Start)")
        vbox_daemon = QVBoxLayout(grp_daemon)
        self.lbl_ctrl_daemon = QLabel("• Trạng thái Daemon: Đang kiểm tra...")
        self.lbl_ctrl_faceid = QLabel("• Công tắc xác thực FaceID: Đang kiểm tra...")
        self.lbl_ctrl_autostart = QLabel("• Tự chạy khi bật máy (Boot Auto-Start): Đang kiểm tra...")
        self.lbl_ctrl_winlock = QLabel("• Tích hợp mở khóa Win + L (System32 DLL): Đang kiểm tra...")
        for lbl in (self.lbl_ctrl_daemon, self.lbl_ctrl_faceid, self.lbl_ctrl_autostart, self.lbl_ctrl_winlock):
            lbl.setStyleSheet("font-size: 13px; padding: 2px 0;")
            vbox_daemon.addWidget(lbl)

        btn_daemon_row = QHBoxLayout()
        btn_start_d = QPushButton("▶ Bật Daemon Ngầm")
        btn_start_d.setStyleSheet("background-color: #059669;")
        btn_start_d.clicked.connect(self.start_daemon_explicit)

        btn_restart_d = QPushButton("🔄 Khởi Động Lại Daemon")
        btn_restart_d.setStyleSheet("background-color: #0284c7;")
        btn_restart_d.clicked.connect(self.restart_daemon_explicit)

        btn_stop_d = QPushButton("⏹ Tắt Hẳn Daemon (Stop)")
        btn_stop_d.setStyleSheet("background-color: #dc2626;")
        btn_stop_d.clicked.connect(self.stop_daemon_explicit)

        btn_daemon_row.addWidget(btn_start_d)
        btn_daemon_row.addWidget(btn_restart_d)
        btn_daemon_row.addWidget(btn_stop_d)
        vbox_daemon.addLayout(btn_daemon_row)
        top_split.addWidget(grp_daemon, stretch=3)

        grp_win = QGroupBox("🪟 Tích Hợp Mở Khóa Windows (Win + L & Mật Khẩu Dự Phòng)")
        vbox_win = QVBoxLayout(grp_win)
        lbl_win_desc = QLabel(
            "Quản lý module C++ Credential Provider (SampleV2CredentialProvider.dll)\n"
            "tích hợp trực tiếp vào màn hình khóa Win + L và màn hình bật máy Windows:"
        )
        lbl_win_desc.setStyleSheet("color: #94a3b8;")
        vbox_win.addWidget(lbl_win_desc)

        btn_install_win = QPushButton("🛡️ Cài Đặt / Cập Nhật Mở Khóa Win + L + Task ONSTART (Admin)")
        btn_install_win.setStyleSheet("background-color: #2563eb; padding: 8px;")
        btn_install_win.clicked.connect(self.install_windows_provider)

        btn_uninstall_win = QPushButton("🗑️ Gỡ Bỏ Tích Hợp Màn Hình Khóa Win + L (Admin)")
        btn_uninstall_win.setStyleSheet("background-color: #7f1d1d; padding: 8px;")
        btn_uninstall_win.clicked.connect(self.uninstall_windows_provider)

        btn_test_unlock = QPushButton("⚡ Test Nhanh Luồng Mở Khóa Win + L (windows-unlock)")
        btn_test_unlock.clicked.connect(lambda: self.run_quick_test_service("windows-unlock"))

        vbox_win.addWidget(btn_install_win)
        vbox_win.addWidget(btn_uninstall_win)
        vbox_win.addWidget(btn_test_unlock)
        top_split.addWidget(grp_win, stretch=2)

        tab_control_layout.addLayout(top_split)

        # Contextual Policy
        grp_policy = QGroupBox("🔐 Cấu Hình Chính Sách Xác Thực Theo Ngữ Cảnh (Contextual Security Policy)")
        grid_pol = QGridLayout(grp_policy)

        sub_unlock = QGroupBox("1. Khi Khóa Màn Hình Tạm Thời (Win + L / windows-unlock)")
        form_unlock = QGridLayout(sub_unlock)
        self.chk_unlock_active = QCheckBox("Yêu cầu thử thách Active Liveness (Chớp mắt/Quay đầu)")
        self.spn_unlock_dec = QDoubleSpinBox()
        self.spn_unlock_dec.setRange(0.40, 0.99)
        self.spn_unlock_dec.setSingleStep(0.02)
        self.spn_unlock_sim = QDoubleSpinBox()
        self.spn_unlock_sim.setRange(0.40, 0.99)
        self.spn_unlock_sim.setSingleStep(0.02)
        self.spn_unlock_timeout = QDoubleSpinBox()
        self.spn_unlock_timeout.setRange(2.0, 15.0)
        self.spn_unlock_timeout.setSingleStep(0.5)

        form_unlock.addWidget(self.chk_unlock_active, 0, 0, 1, 2)
        form_unlock.addWidget(QLabel("Ngưỡng Quyết Định Tổng Hợp (Decision Threshold):"), 1, 0)
        form_unlock.addWidget(self.spn_unlock_dec, 1, 1)
        form_unlock.addWidget(QLabel("Ngưỡng Tương Đồng Khuôn Mặt (ArcFace Sim):"), 2, 0)
        form_unlock.addWidget(self.spn_unlock_sim, 2, 1)
        form_unlock.addWidget(QLabel("Thời Gian Quét Tối Đa (Timeout giây):"), 3, 0)
        form_unlock.addWidget(self.spn_unlock_timeout, 3, 1)
        grid_pol.addWidget(sub_unlock, 0, 0)

        sub_logon = QGroupBox("2. Khi Bật Máy / Đăng Nhập Mới (Boot / windows-logon & sudo)")
        form_logon = QGridLayout(sub_logon)
        self.chk_logon_active = QCheckBox("Yêu cầu thử thách Active Liveness (Chớp mắt/Quay đầu)")
        self.spn_logon_dec = QDoubleSpinBox()
        self.spn_logon_dec.setRange(0.40, 0.99)
        self.spn_logon_dec.setSingleStep(0.02)
        self.spn_logon_sim = QDoubleSpinBox()
        self.spn_logon_sim.setRange(0.40, 0.99)
        self.spn_logon_sim.setSingleStep(0.02)
        self.spn_logon_timeout = QDoubleSpinBox()
        self.spn_logon_timeout.setRange(2.0, 15.0)
        self.spn_logon_timeout.setSingleStep(0.5)

        form_logon.addWidget(self.chk_logon_active, 0, 0, 1, 2)
        form_logon.addWidget(QLabel("Ngưỡng Quyết Định Tổng Hợp (Decision Threshold):"), 1, 0)
        form_logon.addWidget(self.spn_logon_dec, 1, 1)
        form_logon.addWidget(QLabel("Ngưỡng Tương Đồng Khuôn Mặt (ArcFace Sim):"), 2, 0)
        form_logon.addWidget(self.spn_logon_sim, 2, 1)
        form_logon.addWidget(QLabel("Thời Gian Quét Tối Đa (Timeout giây):"), 3, 0)
        form_logon.addWidget(self.spn_logon_timeout, 3, 1)
        grid_pol.addWidget(sub_logon, 0, 1)

        pol_btn_row = QHBoxLayout()
        self.spn_max_fail = QSpinBox()
        self.spn_max_fail.setRange(1, 10)
        self.spn_lockout_sec = QSpinBox()
        self.spn_lockout_sec.setRange(10, 600)
        pol_btn_row.addWidget(QLabel("Số lần sai tối đa trước khi khóa tạm (Rate Limit):"))
        pol_btn_row.addWidget(self.spn_max_fail)
        pol_btn_row.addWidget(QLabel("Thời gian khóa tạm (giây):"))
        pol_btn_row.addWidget(self.spn_lockout_sec)
        pol_btn_row.addStretch()

        btn_save_policy = QPushButton("💾 Lưu Cấu Hình & Áp Dụng Ngay (Hot-Reload)")
        btn_save_policy.setStyleSheet("background-color: #059669; padding: 8px 20px; font-size: 14px;")
        btn_save_policy.clicked.connect(self.save_policy_settings)
        pol_btn_row.addWidget(btn_save_policy)

        grid_pol.addLayout(pol_btn_row, 1, 0, 1, 2)
        tab_control_layout.addWidget(grp_policy)

        self.tabs.addTab(tab_control, "🎛️ Bảng Điều Khiển & Cấu Hình")

        # TAB 2: LIVE MONITOR & METRICS
        tab_monitor = QWidget()
        tab_monitor_layout = QHBoxLayout(tab_monitor)

        preview_group = QGroupBox("Camera Live Feed (Nhận diện thời gian thực)")
        preview_vbox = QVBoxLayout(preview_group)
        self.cam_label = QLabel("Chưa bật xem trước camera\n(Bấm 'Bật Giám Sát Realtime' bên dưới)")
        self.cam_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cam_label.setStyleSheet("background-color: #020617; border-radius: 8px; min-height: 360px;")
        preview_vbox.addWidget(self.cam_label)

        cam_btn_layout = QHBoxLayout()
        self.btn_start_cam = QPushButton("▶ Bật Giám Sát Realtime")
        self.btn_start_cam.setStyleSheet("background-color: #059669;")
        self.btn_start_cam.clicked.connect(self.start_camera_monitor)
        self.btn_stop_cam = QPushButton("⏹ Dừng Camera")
        self.btn_stop_cam.clicked.connect(self.stop_camera_monitor)
        self.btn_stop_cam.setEnabled(False)

        cam_btn_layout.addWidget(self.btn_start_cam)
        cam_btn_layout.addWidget(self.btn_stop_cam)
        preview_vbox.addLayout(cam_btn_layout)
        tab_monitor_layout.addWidget(preview_group, stretch=3)

        telemetry_group = QGroupBox("Chỉ Số Đa Phương Thức (AI Telemetry)")
        telemetry_vbox = QVBoxLayout(telemetry_group)

        self.lbl_fps = QLabel("FPS Xử Lý: 0.0")
        self.lbl_status_det = QLabel("Phát Hiện Khuôn Mặt: Không có")
        self.lbl_fiqa = QLabel("Chất Lượng Ảnh (FIQA): 0.0")
        self.lbl_moire = QLabel("Chống Màn Hình (2D FFT): 0.00")
        self.lbl_pad = QLabel("Chống Giả Mạo (PAD): 0%")
        self.prog_pad = QProgressBar()
        self.prog_pad.setRange(0, 100)

        self.lbl_sim = QLabel("Tương Đồng ArcFace: 0.00")
        self.lbl_fused = QLabel("Độ Tin Cậy Hợp Nhất (Confidence): 0.00")
        self.prog_fused = QProgressBar()
        self.prog_fused.setRange(0, 100)

        self.lbl_result = QLabel("KẾT LUẬN: ĐANG CHỜ")
        self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 6px; border-radius: 4px; background: #334155;")
        self.lbl_result.setAlignment(Qt.AlignmentFlag.AlignCenter)

        telemetry_vbox.addWidget(self.lbl_fps)
        telemetry_vbox.addWidget(self.lbl_status_det)
        telemetry_vbox.addWidget(self.lbl_fiqa)
        telemetry_vbox.addWidget(self.lbl_moire)
        telemetry_vbox.addWidget(self.lbl_pad)
        telemetry_vbox.addWidget(self.prog_pad)
        telemetry_vbox.addWidget(self.lbl_sim)
        telemetry_vbox.addWidget(self.lbl_fused)
        telemetry_vbox.addWidget(self.prog_fused)
        telemetry_vbox.addWidget(self.lbl_result)
        telemetry_vbox.addStretch()

        tab_monitor_layout.addWidget(telemetry_group, stretch=2)
        self.tabs.addTab(tab_monitor, "📊 Giám Sát Camera & AI Realtime")

        # TAB 3: USER MANAGEMENT
        tab_users = QWidget()
        tab_users_layout = QVBoxLayout(tab_users)

        btn_user_row = QHBoxLayout()
        self.btn_refresh_users = QPushButton("🔄 Tải Lại Danh Sách")
        self.btn_refresh_users.clicked.connect(self.refresh_user_list)
        self.btn_enroll_user = QPushButton("➕ Đăng Ký / Cập Nhật Khuôn Mặt (Enroll 5 Góc)")
        self.btn_enroll_user.setStyleSheet("background-color: #2563eb;")
        self.btn_enroll_user.clicked.connect(self.enroll_new_user)
        self.btn_verify_user = QPushButton("🔍 Mở Cửa Sổ Kiểm Thử 1:1 (Verify Interactive)")
        self.btn_verify_user.setStyleSheet("background-color: #0284c7;")
        self.btn_verify_user.clicked.connect(self.verify_selected_user)
        self.btn_delete_user = QPushButton("🗑️ Xóa Người Dùng Đã Chọn")
        self.btn_delete_user.setStyleSheet("background-color: #dc2626;")
        self.btn_delete_user.clicked.connect(self.delete_selected_user)

        btn_user_row.addWidget(self.btn_refresh_users)
        btn_user_row.addWidget(self.btn_enroll_user)
        btn_user_row.addWidget(self.btn_verify_user)
        btn_user_row.addWidget(self.btn_delete_user)
        btn_user_row.addStretch()
        tab_users_layout.addLayout(btn_user_row)

        self.user_table = QTableWidget()
        self.user_table.setColumnCount(4)
        self.user_table.setHorizontalHeaderLabels(["Username", "Số Lượng Prototypes", "Định Dạng Vector", "Trạng Thái"])
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tab_users_layout.addWidget(self.user_table)

        self.tabs.addTab(tab_users, "👥 Quản Lý Khuôn Mặt Người Dùng")

        # TAB 4: AUDIT LOGS
        tab_logs = QWidget()
        tab_logs_layout = QVBoxLayout(tab_logs)

        log_btn_row = QHBoxLayout()
        self.btn_reload_daemon_log = QPushButton("🔄 Tải Log Thực Tế Từ Daemon (data/daemon.log)")
        self.btn_reload_daemon_log.clicked.connect(self.load_daemon_log_file)
        self.btn_quick_test = QPushButton("⚡ Test Xác Thực Nhanh (windows-unlock)")
        self.btn_quick_test.setStyleSheet("background-color: #059669;")
        self.btn_quick_test.clicked.connect(lambda: self.run_quick_test_service("windows-unlock"))
        self.btn_quick_test_sudo = QPushButton("🔐 Test Xác Thực Có Active Liveness (windows-logon)")
        self.btn_quick_test_sudo.setStyleSheet("background-color: #0284c7;")
        self.btn_quick_test_sudo.clicked.connect(lambda: self.run_quick_test_service("windows-logon"))
        self.btn_clear_logs = QPushButton("🧹 Xóa Màn Hình Log")
        self.btn_clear_logs.clicked.connect(lambda: self.log_text.clear())

        log_btn_row.addWidget(self.btn_reload_daemon_log)
        log_btn_row.addWidget(self.btn_quick_test)
        log_btn_row.addWidget(self.btn_quick_test_sudo)
        log_btn_row.addWidget(self.btn_clear_logs)
        log_btn_row.addStretch()
        tab_logs_layout.addLayout(log_btn_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        tab_logs_layout.addWidget(self.log_text)

        self.tabs.addTab(tab_logs, "📜 Nhật Ký Kiểm Toán (Audit Logs)")

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

        if daemon_running:
            self.btn_toggle_daemon.setText(f"🟢 DAEMON: ĐANG CHẠY (PID {pid})")
            self.btn_toggle_daemon.setStyleSheet("background-color: #0284c7; color: white;")
            self.lbl_ctrl_daemon.setText(f"• Trạng thái Daemon: 🟢 ĐANG HOẠT ĐỘNG NGẦM (PID: {pid}, Cổng TCP 9876 OK)")
        else:
            self.btn_toggle_daemon.setText("⚫ DAEMON: ĐÃ TẮT (Bấm để Bật)")
            self.btn_toggle_daemon.setStyleSheet("background-color: #475569; color: white;")
            self.lbl_ctrl_daemon.setText("• Trạng thái Daemon: 🔴 ĐÃ TẮT (Chưa nạp vào RAM)")

        if enabled:
            self.btn_toggle_faceid.setText("🟢 FACEID: BẬT")
            self.btn_toggle_faceid.setStyleSheet("background-color: #059669; color: white;")
            self.lbl_ctrl_faceid.setText("• Công tắc xác thực FaceID: 🟢 ĐANG BẬT (Tự động nhận diện khuôn mặt)")
        else:
            self.btn_toggle_faceid.setText("🔴 FACEID: TẮT (BYPASS)")
            self.btn_toggle_faceid.setStyleSheet("background-color: #dc2626; color: white;")
            self.lbl_ctrl_faceid.setText("• Công tắc xác thực FaceID: 🔴 ĐANG TẮT (Bypass 100% sang mật khẩu dự phòng)")

        if autostart_on:
            self.btn_toggle_autostart.setText("🚀 AUTO-START: BẬT")
            self.btn_toggle_autostart.setStyleSheet("background-color: #0d9488; color: white;")
            self.lbl_ctrl_autostart.setText("• Tự chạy khi bật máy (Boot Auto-Start): 🟢 ĐÃ BẬT (Tự chạy ngầm sau khi Shut down/Restart)")
        else:
            self.btn_toggle_autostart.setText("⚪ AUTO-START: TẮT")
            self.btn_toggle_autostart.setStyleSheet("background-color: #475569; color: white;")
            self.lbl_ctrl_autostart.setText("• Tự chạy khi bật máy (Boot Auto-Start): ⚪ ĐANG TẮT")

        if winlock_ok:
            self.lbl_ctrl_winlock.setText("• Tích hợp mở khóa Win + L (System32 DLL): 🟢 ĐÃ CÀI ĐẶT & ĐĂNG KÝ REGISTRY")
        else:
            self.lbl_ctrl_winlock.setText("• Tích hợp mở khóa Win + L (System32 DLL): ⚪ CHƯA CÀI ĐẶT (hoặc đang chạy trên Linux)")

    def run_ctl_async(self, subcmd: str, busy_text: str, done_msg: str):
        if self.cmd_worker and self.cmd_worker.isRunning():
            return
        self.btn_toggle_daemon.setText(busy_text)
        ctl_script = os.path.join(BASE_DIR, "scripts", "faceidctl")
        self.cmd_worker = CommandRunnerWorker([sys.executable, ctl_script, subcmd], done_msg)
        self.cmd_worker.finished_signal.connect(self.on_ctl_finished)
        self.cmd_worker.start()

    def on_ctl_finished(self, msg: str):
        self.log_message(msg)
        self.trigger_async_status_refresh()

    def start_daemon_explicit(self):
        self.run_ctl_async("start", "⏳ Đang bật Daemon...", "[DAEMON] Đã khởi động tiến trình ngầm FaceID Daemon.")

    def stop_daemon_explicit(self):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        self.run_ctl_async("stop", "⏳ Đang tắt Daemon...", "[DAEMON] Đã dừng hẳn tiến trình ngầm FaceID Daemon.")

    def restart_daemon_explicit(self):
        self.run_ctl_async("restart", "⏳ Đang khởi động lại...", "[DAEMON] Đã khởi động lại tiến trình ngầm FaceID Daemon.")

    def toggle_daemon_process(self):
        if self._cached_status.get("daemon_running", False):
            self.stop_daemon_explicit()
        else:
            self.start_daemon_explicit()

    def toggle_autostart_state(self):
        if self._cached_status.get("autostart", False):
            self.run_ctl_async("disable-autostart", "⏳ Đang cập nhật...", "[AUTO-START] Đã TẮT tự động chạy ngầm khi khởi động máy.")
        else:
            self.run_ctl_async("enable-autostart", "⏳ Đang cập nhật...", "[AUTO-START] Đã BẬT tự động chạy ngầm khi khởi động máy.")

    def toggle_faceid_state(self):
        if not os.path.exists(CONFIG_FILE):
            return
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        current = cfg.get("system", {}).get("enabled", True)
        new_state = not current
        cfg.setdefault("system", {})["enabled"] = new_state
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        fast_query_daemon(action="enable" if new_state else "disable", timeout=0.18)
        self.trigger_async_status_refresh()
        st_text = "BẬT" if new_state else "TẮT (Chuyển sang dùng mật khẩu)"
        self.log_message(f"[CẤU HÌNH] Đã chuyển công tắc FaceID sang: {st_text}")

    def load_policy_settings(self):
        if os.path.exists(POLICY_FILE):
            try:
                with open(POLICY_FILE, "r", encoding="utf-8") as f:
                    pol = json.load(f).get("contextual_policies", {})
                u_cfg = pol.get("windows-unlock", {})
                l_cfg = pol.get("windows-logon", {})

                self.chk_unlock_active.setChecked(bool(u_cfg.get("require_active_liveness", False)))
                self.spn_unlock_dec.setValue(float(u_cfg.get("decision_threshold", 0.68)))
                self.spn_unlock_sim.setValue(float(u_cfg.get("sim_threshold", 0.62)))
                self.spn_unlock_timeout.setValue(float(u_cfg.get("active_timeout_sec", 5.0)))

                self.chk_logon_active.setChecked(bool(l_cfg.get("require_active_liveness", True)))
                self.spn_logon_dec.setValue(float(l_cfg.get("decision_threshold", 0.72)))
                self.spn_logon_sim.setValue(float(l_cfg.get("sim_threshold", 0.64)))
                self.spn_logon_timeout.setValue(float(l_cfg.get("active_timeout_sec", 6.0)))
            except Exception as e:
                self.log_message(f"[-] Lỗi đọc policy.json: {e}")

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                rl = cfg.get("security", {}).get("rate_limiting", {})
                self.spn_max_fail.setValue(int(rl.get("max_consecutive_failures", 3)))
                self.spn_lockout_sec.setValue(int(rl.get("lockout_duration_sec", 45)))
            except Exception:
                pass

    def save_policy_settings(self):
        try:
            if os.path.exists(POLICY_FILE):
                with open(POLICY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pol = data.setdefault("contextual_policies", {})

                u_cfg = pol.setdefault("windows-unlock", {})
                u_cfg["require_active_liveness"] = self.chk_unlock_active.isChecked()
                u_cfg["decision_threshold"] = round(self.spn_unlock_dec.value(), 2)
                u_cfg["sim_threshold"] = round(self.spn_unlock_sim.value(), 2)
                u_cfg["active_timeout_sec"] = round(self.spn_unlock_timeout.value(), 1)

                l_cfg = pol.setdefault("windows-logon", {})
                l_cfg["require_active_liveness"] = self.chk_logon_active.isChecked()
                l_cfg["decision_threshold"] = round(self.spn_logon_dec.value(), 2)
                l_cfg["sim_threshold"] = round(self.spn_logon_sim.value(), 2)
                l_cfg["active_timeout_sec"] = round(self.spn_logon_timeout.value(), 1)

                with open(POLICY_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                rl = cfg.setdefault("security", {}).setdefault("rate_limiting", {})
                rl["max_consecutive_failures"] = self.spn_max_fail.value()
                rl["lockout_duration_sec"] = self.spn_lockout_sec.value()
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)

            fast_query_daemon(action="reload", timeout=0.18)
            self.log_message("[CẤU HÌNH] Đã lưu và Hot-Reload chính sách bảo mật (policy.json & config.json) thành công!")
            QMessageBox.information(
                self, "Đã lưu cấu hình",
                "Đã lưu chính sách bảo mật và áp dụng ngay lập tức cho Daemon (Hot-Reload)!"
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
                count_str = f"{len(arr)} Pose Prototypes"
                fmt_str = f"{arr.shape[1]}-D Normalized"
                status_str = "Sẵn sàng"
            except Exception as e:
                count_str = "Lỗi"
                fmt_str = "-"
                status_str = str(e)

            self.user_table.setItem(row, 0, QTableWidgetItem(user))
            self.user_table.setItem(row, 1, QTableWidgetItem(count_str))
            self.user_table.setItem(row, 2, QTableWidgetItem(fmt_str))
            self.user_table.setItem(row, 3, QTableWidgetItem(status_str))

    def enroll_new_user(self):
        if self.cam_thread and self.cam_thread.isRunning():
            self.stop_camera_monitor()
        default_user = os.environ.get("USERNAME", "ADMIN")
        user, ok = QInputDialog.getText(
            self, "Đăng Ký Khuôn Mặt (5 Góc)",
            "Nhập tên tài khoản Windows/Linux cần đăng ký:",
            text=default_user
        )
        if not ok or not user.strip():
            return
        user = user.strip()

        self.log_message(f"[*] Bắt đầu tiến trình Enroll 5 tư thế cho '{user}'...")
        script = os.path.join(BASE_DIR, "scripts", "enroll.py")
        subprocess.Popen([sys.executable, script, "--user", user], cwd=BASE_DIR)
        QMessageBox.information(
            self, "Hướng dẫn Đăng ký",
            f"Cửa sổ Camera đăng ký 5 góc mặt cho '{user}' đang mở.\n"
            "Sau khi chụp đủ 5 góc, bấm 'Tải Lại Danh Sách' để cập nhật!"
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

        self.btn_start_cam.setEnabled(False)
        self.btn_stop_cam.setEnabled(True)
        self.log_message(f"[*] Đã bật xem trước camera và bộ giám sát AI thời gian thực cho '{user}'.")

    def stop_camera_monitor(self):
        if self.cam_thread:
            self.cam_thread.stop()
            self.cam_thread = None
        self.cam_label.setText("Đã dừng camera.\n(Lưu ý: Hãy dừng camera ở đây trước khi bấm Win + L để Daemon dùng được Camera)")
        self.btn_start_cam.setEnabled(True)
        self.btn_stop_cam.setEnabled(False)
        self.log_message("[*] Đã tắt camera giám sát.")

    def update_camera_frame(self, q_img: QImage):
        pix = QPixmap.fromImage(q_img)
        scaled = pix.scaled(self.cam_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.cam_label.setPixmap(scaled)

    def update_telemetry(self, m: dict):
        self.lbl_fps.setText(f"FPS Xử Lý: {m['fps']}")
        self.lbl_status_det.setText(f"Phát Hiện Khuôn Mặt: {'ĐÃ THẤY' if m['face_detected'] else 'KHÔNG CÓ'}")
        self.lbl_fiqa.setText(f"Chất Lượng Ảnh (FIQA): {m['fiqa_score']:.1f} {'(ĐẠT)' if m['fiqa_passed'] else '(MỜ)'}")
        self.lbl_moire.setText(f"Chống Màn Hình (2D FFT): {m['moire_score']:.2f} {'(MẶT THẬT)' if m['is_live_fft'] else '(NGHI MÀN HÌNH)'}")

        pad_pct = int(m['pad_score'] * 100)
        self.lbl_pad.setText(f"Chống Giả Mạo (MiniFASNet PAD): {pad_pct}%")
        self.prog_pad.setValue(pad_pct)

        self.lbl_sim.setText(f"Tương Đồng ArcFace (Cos): {m['cosine_sim']:.3f}")

        fused_pct = int(m['fused_conf'] * 100)
        self.lbl_fused.setText(f"Độ Tin Cậy Hợp Nhất (Confidence): {fused_pct}%")
        self.prog_fused.setValue(fused_pct)

        if m['decision'] == "GENUINE":
            self.lbl_result.setText("✅ KẾT LUẬN: CHÍNH CHỦ HỢP LỆ (GENUINE)")
            self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 6px; border-radius: 4px; background: #059669; color: white;")
        elif m['decision'] == "REJECTED":
            self.lbl_result.setText("❌ KẾT LUẬN: TỪ CHỐI / GIẢ MẠO (REJECTED)")
            self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 6px; border-radius: 4px; background: #dc2626; color: white;")
        else:
            self.lbl_result.setText("⏸ KẾT LUẬN: CHƯA THẤY KHUÔN MẶT")
            self.lbl_result.setStyleSheet("font-size: 15px; font-weight: bold; padding: 6px; border-radius: 4px; background: #334155; color: #cbd5e1;")

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
