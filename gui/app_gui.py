"""
FaceID Admin Dashboard - Giao dien quan tri do hoa hien dai bang PyQt6.
Chuc nang:
1. Giam sat he thong (Trang thai On/Off, Daemon socket, Camera status)
2. Thong so Realtime (Quality FIQA, FFT Moire, Anti-Spoof PAD, ArcFace, Fused Score)
3. Cong tac chuyen doi On/Off FaceID tuc thi
4. Quan ly nguoi dung (Xem danh sach, Dang ky moi voi huong dan truc quan, Xoa template)
5. Nhat ky Audit Log thoi gian thuc
6. Kiem thu nhanh xac thuc (Quick Test 1:1)
"""

import os
import sys
import json
import time
import subprocess
import numpy as np
import cv2

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QPixmap, QFont, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
    QProgressBar, QGroupBox, QTabWidget, QHeaderView, QMessageBox,
    QInputDialog, QFrame, QSplitter
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.fhs import get_fhs_path
from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.fusion import MultiModalFusionEngine

CONFIG_FILE = get_fhs_path("config.json", category="config")
EMB_DIR = get_fhs_path("embeddings", category="data")


class CameraMonitorThread(QThread):
    """Thread chay camera background de cap nhat hinh anh va chi so AI realtime."""
    frame_signal = pyqtSignal(QImage)
    metrics_signal = pyqtSignal(dict)

    def __init__(self, target_user="son"):
        super().__init__()
        self.target_user = target_user
        self.running = True
        self.detector = None
        self.fiqa = None
        self.anti_spoof = None
        self.embedder = None
        self.fusion = None
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
            self.detector = YuNetDetector()
            self.fiqa = FaceQualityAssessor()
            self.anti_spoof = MiniFASNetDetector(real_threshold=0.60)
            self.embedder = ArcFaceEmbedder()
            self.fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.75)
            self.load_templates()
        except Exception as e:
            print(f"[-] Loi khoi tao AI trong Monitor Thread: {e}")
            return

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return

        last_time = time.time()
        fps = 0.0

        while self.running:
            ret, frame = cap.read()
            if not ret or frame is None:
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
                "fiqa_score": 0.0,
                "moire_score": 0.0,
                "is_live_fft": True,
                "pad_score": 0.0,
                "cosine_sim": 0.0,
                "fused_conf": 0.0,
                "decision": "NO_FACE"
            }

            detections = self.detector.detect(frame)
            if len(detections) >= 1:
                box, landmarks, _ = detections[0]
                top, right, bottom, left = box
                metrics["face_detected"] = True

                # FIQA
                q = self.fiqa.evaluate_quality(frame, box, landmarks)
                metrics["fiqa_passed"] = q.passed
                metrics["fiqa_score"] = float(q.sharpness)
                metrics["moire_score"] = float(q.moire_score)
                metrics["is_live_fft"] = q.is_live_fft

                # PAD
                _, pad_score, _ = self.anti_spoof.predict_smoothed(frame, box)
                metrics["pad_score"] = float(pad_score)

                # EMB & FUS
                if self.templates is not None and len(self.templates) > 0:
                    emb = self.embedder.extract(frame, box, landmarks)
                    sims = [1.0 - self.embedder.compute_distance(emb, t) for t in self.templates]
                    best_sim = float(max(sims))
                    metrics["cosine_sim"] = best_sim

                    effective_passive = pad_score if q.is_live_fft else (pad_score * 0.82)
                    dec = self.fusion.evaluate_fused(
                        passive_score=effective_passive,
                        active_passed=True,
                        probe_emb=emb,
                        template_emb=self.templates[np.argmax(sims)],
                        raw_cosine_sim=best_sim,
                        best_user=self.target_user,
                        fiqa_score=1.0 if q.passed else 0.5
                    )
                    metrics["fused_conf"] = float(dec.confidence_score)
                    metrics["decision"] = "GENUINE" if dec.confidence_score >= 0.75 else "REJECTED"

                # Draw overlay
                color = (0, 255, 0) if metrics["decision"] == "GENUINE" else (0, 0, 255)
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                lbl = f"{self.target_user}: {metrics['fused_conf']:.2f}"
                cv2.putText(frame, lbl, (left, max(20, top - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            self.metrics_signal.emit(metrics)

            # Convert to QImage
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            q_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            self.frame_signal.emit(q_img.copy())

            time.sleep(0.02)

        cap.release()

    def stop(self):
        self.running = False
        self.wait(1000)


class FaceIDAdminWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FaceID Hybrid - Trung Tâm Quản Trị Hệ Thống (PyQt6 Admin Dashboard)")
        self.resize(1100, 720)
        self.cam_thread = None

        self.apply_dark_theme()
        self.init_ui()
        self.refresh_status()
        self.refresh_user_list()

    def apply_dark_theme(self):
        dark_qss = """
        QMainWindow { background-color: #0f172a; }
        QWidget { color: #e2e8f0; font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
        QGroupBox { border: 1px solid #334155; border-radius: 8px; margin-top: 12px; font-weight: bold; padding: 12px; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; color: #38bdf8; }
        QPushButton { background-color: #1e293b; border: 1px solid #475569; border-radius: 6px; padding: 6px 14px; font-weight: bold; }
        QPushButton:hover { background-color: #334155; border-color: #38bdf8; }
        QPushButton:pressed { background-color: #0284c7; }
        QTableWidget { background-color: #1e293b; border: 1px solid #334155; gridline-color: #334155; border-radius: 6px; }
        QHeaderView::section { background-color: #0f172a; color: #94a3b8; font-weight: bold; border: none; padding: 6px; }
        QTextEdit { background-color: #090d16; border: 1px solid #1e293b; border-radius: 6px; font-family: 'Consolas', monospace; color: #a5f3fc; }
        QProgressBar { border: 1px solid #334155; border-radius: 4px; text-align: center; background-color: #1e293b; }
        QProgressBar::chunk { background-color: #0284c7; }
        QTabWidget::pane { border: 1px solid #334155; border-radius: 8px; }
        QTabBar::tab { background: #1e293b; color: #94a3b8; padding: 8px 20px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
        QTabBar::tab:selected { background: #0284c7; color: #ffffff; font-weight: bold; }
        """
        self.setStyleSheet(dark_qss)

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)

        # Header bar
        header_layout = QHBoxLayout()
        title_lbl = QLabel("🛡️ FACEID HYBRID AUTHENTICATION - MANAGEMENT DASHBOARD")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #38bdf8;")
        header_layout.addWidget(title_lbl)

        header_layout.addStretch()

        self.btn_toggle_faceid = QPushButton("Đang tải...")
        self.btn_toggle_faceid.setMinimumWidth(160)
        self.btn_toggle_faceid.clicked.connect(self.toggle_faceid_state)
        header_layout.addWidget(self.btn_toggle_faceid)

        main_layout.addLayout(header_layout)

        # Tabs
        self.tabs = QTabWidget()

        # Tab 1: Live Monitor & Metrics
        tab_monitor = QWidget()
        tab_monitor_layout = QHBoxLayout(tab_monitor)

        # Left: Camera Preview
        preview_group = QGroupBox("Camera Live Feed (Nhận diện thời gian thực)")
        preview_vbox = QVBoxLayout(preview_group)
        self.cam_label = QLabel("Chưa bật xem trước camera\n(Bấm 'Bật Giám Sát Realtime' bên phải)")
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

        # Right: Realtime AI Telemetry
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
        self.tabs.addTab(tab_monitor, "📊 Giám Sát Realtime & AI")

        # Tab 2: User Management
        tab_users = QWidget()
        tab_users_layout = QVBoxLayout(tab_users)

        btn_user_row = QHBoxLayout()
        self.btn_refresh_users = QPushButton("🔄 Tải Lại Danh Sách")
        self.btn_refresh_users.clicked.connect(self.refresh_user_list)
        self.btn_enroll_user = QPushButton("➕ Đăng Ký Khuôn Mặt Mới (Enroll 5 Góc)")
        self.btn_enroll_user.setStyleSheet("background-color: #2563eb;")
        self.btn_enroll_user.clicked.connect(self.enroll_new_user)
        self.btn_delete_user = QPushButton("🗑️ Xóa Người Dùng Đã Chọn")
        self.btn_delete_user.setStyleSheet("background-color: #dc2626;")
        self.btn_delete_user.clicked.connect(self.delete_selected_user)

        btn_user_row.addWidget(self.btn_refresh_users)
        btn_user_row.addWidget(self.btn_enroll_user)
        btn_user_row.addWidget(self.btn_delete_user)
        btn_user_row.addStretch()
        tab_users_layout.addLayout(btn_user_row)

        self.user_table = QTableWidget()
        self.user_table.setColumnCount(4)
        self.user_table.setHorizontalHeaderLabels(["Username", "Số Lượng Prototypes", "Định Dạng Vector", "Trạng Thái"])
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tab_users_layout.addWidget(self.user_table)

        self.tabs.addTab(tab_users, "👥 Quản Lý Người Dùng")

        # Tab 3: System Logs & Audit
        tab_logs = QWidget()
        tab_logs_layout = QVBoxLayout(tab_logs)

        log_btn_row = QHBoxLayout()
        self.btn_clear_logs = QPushButton("🧹 Xóa Màn Hình Log")
        self.btn_clear_logs.clicked.connect(lambda: self.log_text.clear())
        self.btn_quick_test = QPushButton("⚡ Thử Nghiệm Xác Thực Nhanh (sudo)")
        self.btn_quick_test.clicked.connect(self.run_quick_test)

        log_btn_row.addWidget(self.btn_quick_test)
        log_btn_row.addWidget(self.btn_clear_logs)
        log_btn_row.addStretch()
        tab_logs_layout.addLayout(log_btn_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        tab_logs_layout.addWidget(self.log_text)

        self.tabs.addTab(tab_logs, "📜 Nhật Ký Kiểm Toán (Audit Logs)")

        main_layout.addWidget(self.tabs)
        self.setCentralWidget(main_widget)

        self.log_message("[HỆ THỐNG] FaceID Admin Dashboard khởi tạo thành công.")

    def log_message(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")

    def refresh_status(self):
        enabled = True
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    enabled = cfg.get("system", {}).get("enabled", True)
            except Exception:
                pass

        if enabled:
            self.btn_toggle_faceid.setText("🟢 FACEID: BẬT")
            self.btn_toggle_faceid.setStyleSheet("background-color: #059669; color: white;")
        else:
            self.btn_toggle_faceid.setText("🔴 FACEID: TẮT (BYPASS)")
            self.btn_toggle_faceid.setStyleSheet("background-color: #dc2626; color: white;")

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

        self.refresh_status()
        st_text = "BẬT" if new_state else "TẮT (Chuyển sang dùng mật khẩu)"
        self.log_message(f"[CẤU HÌNH] Đã chuyển công tắc FaceID sang: {st_text}")
        QMessageBox.information(self, "Thông báo", f"Đã chuyển trạng thái FaceID sang: {st_text}")

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
        user, ok = QInputDialog.getText(self, "Đăng Ký Người Dùng Mới", "Nhập tên tài khoản (viết thường, không dấu):")
        if not ok or not user.strip():
            return
        user = user.strip().lower()

        self.log_message(f"[*] Bắt đầu tiến trình Enroll 5 tư thế cho '{user}'...")
        script = os.path.join(BASE_DIR, "scripts", "enroll.py")
        subprocess.Popen([sys.executable, script, "--user", user])
        QMessageBox.information(self, "Hướng dẫn", f"Cửa sổ hướng dẫn 5 góc mặt đang bật.\nVui lòng nhìn vào camera làm theo hướng dẫn!")
        self.refresh_user_list()

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

    def run_quick_test(self):
        curr_row = self.user_table.currentRow()
        user = "son"
        if curr_row >= 0:
            user = self.user_table.item(curr_row, 0).text()

        self.log_message(f"[*] Bắt đầu kiểm thử PAM sudo cho user '{user}'...")
        client_script = os.path.join(BASE_DIR, "scripts", "faceid_pam_client.py")
        res = subprocess.run([sys.executable, client_script, user, "sudo"], capture_output=True, text=True)
        if res.returncode == 0:
            self.log_message(f"[+] KẾT QUẢ TEST: XÁC THỰC THÀNH CÔNG (Exit code 0).")
            QMessageBox.information(self, "Kết Quả", f"Xác thực PAM Thành Công cho '{user}' (Exit 0)!")
        else:
            err = res.stderr.strip() or "Khuôn mặt không khớp"
            self.log_message(f"[-] KẾT QUẢ TEST: THẤT BẠI / FALLBACK ({err}) (Exit code 1).")
            QMessageBox.warning(self, "Kết Quả", f"Xác thực Thất Bại -> Fallback Password (Exit 1)!\n{err}")

    def start_camera_monitor(self):
        if self.cam_thread and self.cam_thread.isRunning():
            return
        curr_row = self.user_table.currentRow()
        user = "son"
        if curr_row >= 0:
            user = self.user_table.item(curr_row, 0).text()

        self.cam_thread = CameraMonitorThread(target_user=user)
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
        self.cam_label.setText("Đã dừng camera.")
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
