"""
FaceID Lock Screen Overlay & Authentication UI (PyQt6)
Giao dien man hinh khoa hien dai mo phong Windows Hello / Apple FaceID:
- Man hinh toi mo kinh mo (Backdrop Blur / Dark Acrylic)
- Dong ho lon, ngay thang
- Avatar nguoi dung + Icon FaceID voi radar animation
- Huong dan Liveness / Trang thai quet thoi gian thuc
- Tu dong mo khoa khi xac thuc dung
- Fallback nhap mat khau khi that bai hoac bam ESC / Password button
"""

import os
import sys
import time
import numpy as np
import cv2

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QRect
from PyQt6.QtGui import QImage, QPixmap, QFont, QColor, QPainter, QBrush, QPen
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFrame, QGraphicsDropShadowEffect
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.fhs import get_fhs_path
from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.liveness import ChallengeResponseDetector
from core.fusion import MultiModalFusionEngine

EMB_DIR = get_fhs_path("embeddings", category="data")


class FaceAuthWorker(QThread):
    """Thread chay ngam quet camera, Liveness va ArcFace de mo khoa."""
    status_signal = pyqtSignal(str, str, str)  # (status_type, message, detail)
    success_signal = pyqtSignal(float)          # confidence
    failed_signal = pyqtSignal(str)             # reason

    def __init__(self, target_user="son", service="sudo", require_liveness=True):
        super().__init__()
        self.target_user = target_user
        self.service = service
        self.require_liveness = require_liveness
        self.running = True

    def run(self):
        # Load user templates
        user_path = os.path.join(EMB_DIR, f"{self.target_user}.npy")
        if not os.path.exists(user_path):
            self.failed_signal.emit(f"Chưa đăng ký khuôn mặt cho '{self.target_user}'")
            return

        try:
            templates = np.load(user_path, allow_pickle=False)
        except Exception as e:
            self.failed_signal.emit(f"Lỗi đọc template: {e}")
            return

        self.status_signal.emit("SEARCHING", "Đang tìm kiếm khuôn mặt...", "")

        try:
            detector = YuNetDetector()
            fiqa = FaceQualityAssessor()
            anti_spoof = MiniFASNetDetector(real_threshold=0.60)
            embedder = ArcFaceEmbedder()
            fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.75)
            challenge = ChallengeResponseDetector(timeout_sec=10.0, preload_hands=False)
        except Exception as e:
            self.failed_signal.emit(f"Lỗi nạp AI: {e}")
            return

        require_active = self.require_liveness
        if require_active:
            chosen_action = challenge.reset_challenge()
            self.status_signal.emit("CHALLENGE", f"{chosen_action.value}", "Vui lòng thực hiện cử động trước webcam")

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.failed_signal.emit("Không thể truy cập camera (Camera bận hoặc bị ngắt kết nối)")
            return

        t_start = time.time()
        active_passed = not require_active
        timeout_sec = 6.0

        while self.running and (time.time() - t_start < timeout_sec):
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue

            detections = detector.detect(frame)
            if len(detections) != 1:
                if len(detections) == 0:
                    self.status_signal.emit("SEARCHING", "Đang tìm kiếm khuôn mặt...", "")
                else:
                    self.status_signal.emit("WARNING", "Phát hiện nhiều khuôn mặt!", "Vui lòng đứng một mình")
                continue

            box, landmarks, _ = detections[0]
            quality = fiqa.evaluate_quality(frame, box, landmarks)
            pose = (quality.yaw, quality.pitch, quality.roll)

            # Liveness Challenge
            if not active_passed:
                p, hint, _ = challenge.check_action(frame, pose, landmarks, box)
                if hint:
                    self.status_signal.emit("CHALLENGE", f"Vui lòng: {hint}", "Đang theo dõi cử động...")
                if p:
                    active_passed = True
                    self.status_signal.emit("VERIFYING", "Cử động hợp lệ! Đang nhận diện...", "")

            if active_passed:
                if not quality.passed:
                    self.status_signal.emit("WARNING", "Chất lượng hình ảnh chưa đạt", "Vui lòng nhìn thẳng và đủ sáng")
                    continue

                is_real, pad_score, _ = anti_spoof.predict_smoothed(frame, box)
                emb = embedder.extract(frame, box, landmarks)

                sims = [1.0 - embedder.compute_distance(emb, t) for t in templates]
                best_sim = float(max(sims))

                effective_passive = pad_score if quality.is_live_fft else (pad_score * 0.82)
                decision = fusion.evaluate_fused(
                    passive_score=effective_passive,
                    active_passed=True,
                    probe_emb=emb,
                    template_emb=templates[np.argmax(sims)],
                    raw_cosine_sim=best_sim,
                    best_user=self.target_user,
                    fiqa_score=1.0 if quality.passed else 0.5
                )

                if decision.confidence_score >= 0.65:
                    cap.release()
                    self.success_signal.emit(float(decision.confidence_score))
                    return
                elif pad_score < 0.30 or not quality.is_live_fft:
                    self.status_signal.emit("SPOOF", "CẢNH BÁO: PHÁT HIỆN GIẢ MẠO!", "Từ chối xác thực ảnh in/màn hình")
                    time.sleep(1.0)
                    cap.release()
                    self.failed_signal.emit("Phát hiện giả mạo khuôn mặt (Presentation Attack)")
                    return

            time.sleep(0.02)

        cap.release()
        self.failed_signal.emit("Hết thời gian chờ (Không nhận diện được khuôn mặt)")

    def stop(self):
        self.running = False
        self.wait(1000)


class FaceIDLockScreen(QWidget):
    def __init__(self, target_user="son", service="sudo", require_liveness=True):
        super().__init__()
        self.target_user = target_user
        self.service = service
        self.require_liveness = require_liveness
        self.auth_worker = None

        self.init_window()
        self.init_ui()
        self.start_clock_timer()
        self.start_auth()

    def init_window(self):
        self.setWindowTitle("Windows Lock Screen - FaceID Authentication")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        
        # Man hinh kich thuoc lon mo phong Lock Screen
        self.resize(1024, 700)
        self.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #090d16, stop:1 #111827);
                color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
        """)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(20)

        # 1. Dong ho gio & ngay thang lon o goc tren
        clock_layout = QVBoxLayout()
        self.lbl_time = QLabel("12:00")
        self.lbl_time.setStyleSheet("font-size: 72px; font-weight: 300; color: #f8fafc;")
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_date = QLabel("Thứ Năm, 22 Tháng 10")
        self.lbl_date.setStyleSheet("font-size: 20px; font-weight: 400; color: #94a3b8;")
        self.lbl_date.setAlignment(Qt.AlignmentFlag.AlignCenter)

        clock_layout.addWidget(self.lbl_time)
        clock_layout.addWidget(self.lbl_date)
        main_layout.addLayout(clock_layout)

        main_layout.addStretch(1)

        # 2. Khung xac thuc o giua (Avatar + Icon FaceID + Radar)
        center_frame = QFrame()
        center_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 41, 59, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 24px;
                padding: 24px;
            }
        """)
        center_layout = QVBoxLayout(center_frame)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon FaceID / Ổ khóa
        self.lbl_icon = QLabel("👤")
        self.lbl_icon.setStyleSheet("""
            font-size: 64px;
            background-color: #1e293b;
            border: 3px solid #38bdf8;
            border-radius: 50px;
            min-width: 100px;
            max-width: 100px;
            min-height: 100px;
            max-height: 100px;
        """)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.lbl_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        # Username
        self.lbl_username = QLabel(self.target_user.upper())
        self.lbl_username.setStyleSheet("font-size: 22px; font-weight: bold; color: #f8fafc; margin-top: 10px;")
        self.lbl_username.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.lbl_username)

        # Dòng trạng thái xác thực
        self.lbl_status = QLabel("Đang kích hoạt FaceID...")
        self.lbl_status.setStyleSheet("font-size: 16px; font-weight: 500; color: #38bdf8; margin-top: 6px;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.lbl_status)

        # Dòng phụ chú
        self.lbl_detail = QLabel("Vui lòng nhìn thẳng vào camera")
        self.lbl_detail.setStyleSheet("font-size: 13px; color: #94a3b8;")
        self.lbl_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_layout.addWidget(self.lbl_detail)

        # Ô nhập mật khẩu dự phòng (ẩn mặc định, chỉ hiện khi cần)
        self.password_frame = QFrame()
        pwd_layout = QHBoxLayout(self.password_frame)
        pwd_layout.setContentsMargins(0, 10, 0, 0)
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Nhập mật khẩu dự phòng...")
        self.txt_password.setStyleSheet("""
            QLineEdit {
                background: #0f172a;
                border: 1px solid #475569;
                border-radius: 8px;
                padding: 10px 16px;
                font-size: 14px;
                min-width: 240px;
            }
            QLineEdit:focus { border-color: #38bdf8; }
        """)
        self.btn_submit_pwd = QPushButton("➔")
        self.btn_submit_pwd.setStyleSheet("""
            QPushButton {
                background: #0284c7;
                border-radius: 8px;
                padding: 10px 16px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover { background: #0369a1; }
        """)
        self.btn_submit_pwd.clicked.connect(self.handle_password_login)
        pwd_layout.addWidget(self.txt_password)
        pwd_layout.addWidget(self.btn_submit_pwd)
        self.password_frame.setVisible(False)
        center_layout.addWidget(self.password_frame)

        main_layout.addWidget(center_frame, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addStretch(1)

        # 3. Nút chuyển đổi phương thức ở dưới đáy
        bottom_layout = QHBoxLayout()
        self.btn_fallback_pwd = QPushButton("🔑 Nhập mật khẩu thay thế")
        self.btn_fallback_pwd.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 20px;
                padding: 8px 20px;
                color: #cbd5e1;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.1); border-color: #ffffff; }
        """)
        self.btn_fallback_pwd.clicked.connect(self.show_password_field)

        self.btn_exit = QPushButton("✕ Đóng (Thoát)")
        self.btn_exit.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 20px;
                padding: 8px 20px;
                color: #ef4444;
            }
            QPushButton:hover { background: rgba(239, 68, 68, 0.2); border-color: #ef4444; }
        """)
        self.btn_exit.clicked.connect(self.close)

        bottom_layout.addWidget(self.btn_fallback_pwd)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_exit)
        main_layout.addLayout(bottom_layout)

    def start_clock_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_clock)
        self.timer.start(1000)
        self.update_clock()

    def update_clock(self):
        self.lbl_time.setText(time.strftime("%H:%M"))
        self.lbl_date.setText(time.strftime("%A, %d/%m/%Y"))

    def start_auth(self):
        self.auth_worker = FaceAuthWorker(target_user=self.target_user, service=self.service, require_liveness=self.require_liveness)
        self.auth_worker.status_signal.connect(self.on_auth_status)
        self.auth_worker.success_signal.connect(self.on_auth_success)
        self.auth_worker.failed_signal.connect(self.on_auth_failed)
        self.auth_worker.start()

    def on_auth_status(self, status_type, message, detail):
        self.lbl_status.setText(message)
        self.lbl_detail.setText(detail)

        if status_type == "SEARCHING":
            self.lbl_icon.setText("🔍")
            self.lbl_icon.setStyleSheet("font-size: 54px; background-color: #1e293b; border: 3px solid #38bdf8; border-radius: 50px;")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: 500; color: #38bdf8; margin-top: 6px;")
        elif status_type == "CHALLENGE":
            self.lbl_icon.setText("👁️")
            self.lbl_icon.setStyleSheet("font-size: 54px; background-color: #1e293b; border: 3px solid #eab308; border-radius: 50px;")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #facc15; margin-top: 6px;")
        elif status_type == "WARNING":
            self.lbl_icon.setText("⚠️")
            self.lbl_icon.setStyleSheet("font-size: 54px; background-color: #1e293b; border: 3px solid #f97316; border-radius: 50px;")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: 500; color: #fb923c; margin-top: 6px;")
        elif status_type == "SPOOF":
            self.lbl_icon.setText("🚫")
            self.lbl_icon.setStyleSheet("font-size: 54px; background-color: #1e293b; border: 3px solid #ef4444; border-radius: 50px;")
            self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #f87171; margin-top: 6px;")

    def on_auth_success(self, confidence):
        self.lbl_icon.setText("🔓")
        self.lbl_icon.setStyleSheet("font-size: 54px; background-color: #064e3b; border: 3px solid #10b981; border-radius: 50px;")
        self.lbl_status.setText("XÁC THỰC THÀNH CÔNG!")
        self.lbl_status.setStyleSheet("font-size: 18px; font-weight: bold; color: #34d399; margin-top: 6px;")
        self.lbl_detail.setText(f"Độ tin cậy: {confidence:.1%} — Chào mừng trở lại!")

        # Tu dong truyen hieu ung mo khoa va tat sau 1.2 giay
        QTimer.singleShot(1200, self.unlock_and_close)

    def on_auth_failed(self, reason):
        self.lbl_icon.setText("🔒")
        self.lbl_icon.setStyleSheet("font-size: 54px; background-color: #450a0a; border: 3px solid #ef4444; border-radius: 50px;")
        self.lbl_status.setText("KHÔNG NHẬN DIỆN ĐƯỢC")
        self.lbl_status.setStyleSheet("font-size: 16px; font-weight: bold; color: #f87171; margin-top: 6px;")
        self.lbl_detail.setText(f"{reason} — Vui lòng nhập mật khẩu")
        self.show_password_field()

    def show_password_field(self):
        self.password_frame.setVisible(True)
        self.btn_fallback_pwd.setVisible(False)
        self.txt_password.setFocus()

    def handle_password_login(self):
        # Gia lap fallback mat khau
        pwd = self.txt_password.text()
        if pwd:
            self.lbl_status.setText("MẬT KHẨU HỢP LỆ!")
            self.lbl_status.setStyleSheet("font-size: 18px; font-weight: bold; color: #34d399;")
            QTimer.singleShot(800, self.unlock_and_close)
        else:
            self.lbl_detail.setText("Vui lòng nhập mật khẩu!")

    def unlock_and_close(self):
        if self.auth_worker:
            self.auth_worker.stop()
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.unlock_and_close()
        else:
            super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    target_user = "son"
    if len(sys.argv) > 1:
        target_user = sys.argv[1]

    win = FaceIDLockScreen(target_user=target_user)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
