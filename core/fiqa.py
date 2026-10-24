from dataclasses import dataclass
import cv2
import numpy as np


@dataclass
class QualityMetrics:
    passed: bool
    is_live_fft: bool
    sharpness: float
    brightness: float
    moire_score: float
    yaw: float
    pitch: float
    roll: float
    reason: str

    @property
    def moiré_score(self) -> float:
        """Ho tro ca 2 cach viet moire_score va moiré_score."""
        return self.moire_score


class FaceQualityAssessor:
    def __init__(
        self,
        min_sharpness: float = 35.0,
        min_brightness: float = 35.0,
        max_brightness: float = 235.0,
        max_yaw: float = 25.0,
        max_pitch: float = 22.0,
        max_roll: float = 20.0,
        moire_threshold: float = 0.50
    ):
        self.min_sharpness = min_sharpness
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.max_yaw = max_yaw
        self.max_pitch = max_pitch
        self.max_roll = max_roll
        self.moire_threshold = moire_threshold

    @staticmethod
    def detect_moire_fft(face_roi_gray: np.ndarray) -> float:
        """
        Phat hien van Moire man hinh bang Pho bien thien thich ung (Adaptive Spectral Peak Detection).
        Khong dung vanh mask co dinh (de bi fail voi man hinh Retina / khoang cach khac nhau).
        Su dung cua so Hanning, loai bo truc toa do (cardinal cross axes) va tan so thap DC,
        sau do do do vuot troi (Prominence) cua dinh pho trong cac dai ban kinh dong tam.
        Tra ve diem xac suat Moire trong khoang [0.0, 1.0].
        """
        h, w = face_roi_gray.shape[:2]
        if h < 40 or w < 40:
            return 0.0

        # Lay dung 50% vung trung tam (mui va ma) de triet tieu toc, vien kinh
        y1, y2 = int(h * 0.25), int(h * 0.75)
        x1, x2 = int(w * 0.25), int(w * 0.75)
        center_patch = face_roi_gray[y1:y2, x1:x2]

        if center_patch.size == 0:
            center_patch = face_roi_gray

        N = 128
        resized = cv2.resize(center_patch, (N, N)).astype(np.float32)

        # Ap dung 2D Hanning window triet tieu leakage do vien anh
        win1d = np.hanning(N)
        win2d = np.outer(win1d, win1d)
        windowed = (resized - np.mean(resized)) * win2d

        # 2D FFT & shift
        f = np.fft.fftshift(np.fft.fft2(windowed))
        magnitude = np.abs(f)

        ch, cw = N // 2, N // 2
        y_idx, x_idx = np.ogrid[:N, :N]
        r = np.round(np.sqrt((x_idx - cw) ** 2 + (y_idx - ch) ** 2)).astype(int)

        # Loai bo 2 truc toa do chu thap (cardinal cross axes) vi khuon mat tu nhien
        # luon co nang luong tap trung o duong ngang/doc (mat, mui, mieng)
        cross_axes = (np.abs(x_idx - cw) <= 2) | (np.abs(y_idx - ch) <= 2)

        # Quet qua tat ca cac dai ban kinh tu 8 den 58 (thich ung cho moi DPI tu LCD thuong den Retina OLED)
        max_prominence = 1.0
        for rad in range(8, 58):
            band = (r == rad) & (~cross_axes)
            if np.sum(band) >= 8:
                vals = magnitude[band]
                med = float(np.median(vals)) + 1e-5
                prom = float(np.max(vals)) / med
                if prom > max_prominence:
                    max_prominence = prom

        # Sigmoid calibration:
        # Khuon mat that tu nhien: max_prominence thuong dao dong 3.5 - 6.5 -> score < 0.30
        # Luoi pixel man hinh / Moire: xuat hien dinh harmonic nhon, max_prominence > 15 - 50 -> score > 0.85
        moire_score = float(1.0 / (1.0 + np.exp(-(max_prominence - 8.0) / 1.5)))
        return float(np.clip(moire_score, 0.0, 1.0))

    @staticmethod
    def calculate_sharpness(gray_roi: np.ndarray) -> float:
        if gray_roi.size == 0:
            return 0.0
        return float(cv2.Laplacian(gray_roi, cv2.CV_64F).var())

    @staticmethod
    def calculate_brightness(gray_roi: np.ndarray) -> float:
        if gray_roi.size == 0:
            return 0.0
        return float(np.mean(gray_roi))

    @staticmethod
    def estimate_pose(landmarks: np.ndarray, frame_w: int, frame_h: int, yaw_sign: int = 1, pitch_sign: int = -1) -> tuple[float, float, float]:
        re, le, nose, rm, lm = landmarks[:5]
        d_eye = le - re
        roll = float(np.degrees(np.arctan2(d_eye[1], d_eye[0])))

        dist_r = float(np.linalg.norm(nose - re))
        dist_l = float(np.linalg.norm(nose - le))
        total_eye_dist = dist_r + dist_l + 1e-6
        yaw = float(((dist_r - dist_l) / total_eye_dist) * 90.0) * yaw_sign

        eye_center = (re + le) / 2.0
        mouth_center = (rm + lm) / 2.0
        face_height = float(np.linalg.norm(mouth_center - eye_center)) + 1e-6
        nose_relative = float(nose[1] - eye_center[1])
        actual_ratio = nose_relative / face_height
        pitch = float((actual_ratio - 0.42) * 110.0) * pitch_sign

        return yaw, pitch, roll

    def evaluate_quality(
        self,
        frame_bgr: np.ndarray,
        box: tuple[int, int, int, int],
        landmarks: np.ndarray
    ) -> QualityMetrics:
        h, w = frame_bgr.shape[:2]
        top, right, bottom, left = [int(v) for v in box]

        y1, y2 = max(0, top), min(h, bottom)
        x1, x2 = max(0, left), min(w, right)

        if y2 <= y1 or x2 <= x1:
            return QualityMetrics(
                passed=False, is_live_fft=False, sharpness=0.0,
                brightness=0.0, moiré_score=0.0, yaw=0.0, pitch=0.0, roll=0.0,
                reason="Vung mat khong hop le"
            )

        face_roi = frame_bgr[y1:y2, x1:x2]
        gray_roi = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)

        sharpness = self.calculate_sharpness(gray_roi)
        brightness = self.calculate_brightness(gray_roi)
        moire_score = self.detect_moire_fft(gray_roi)
        yaw, pitch, roll = self.estimate_pose(landmarks, w, h, yaw_sign=-1)

        # 1. Kiem tra van Moire (Adaptive Spectral Peak Detection)
        # FFT Moire dong vai tro soft-indicator ho tro MiniFASNet, KHONG dung lam Hard-Reject doc lap
        is_live_fft = moire_score < self.moire_threshold

        # 2. Kiem tra do mo (Sharpness)
        if sharpness < self.min_sharpness:
            return QualityMetrics(
                passed=False, is_live_fft=is_live_fft, sharpness=sharpness,
                brightness=brightness, moire_score=moire_score,
                yaw=yaw, pitch=pitch, roll=roll,
                reason=f"Anh bi mo ({sharpness:.1f} < {self.min_sharpness})"
            )

        # 3. Kiem tra do sang (Brightness)
        if brightness < self.min_brightness:
            return QualityMetrics(
                passed=False, is_live_fft=is_live_fft, sharpness=sharpness,
                brightness=brightness, moire_score=moire_score,
                yaw=yaw, pitch=pitch, roll=roll,
                reason=f"Mat qua toi ({brightness:.1f} < {self.min_brightness})"
            )
        if brightness > self.max_brightness:
            return QualityMetrics(
                passed=False, is_live_fft=is_live_fft, sharpness=sharpness,
                brightness=brightness, moire_score=moire_score,
                yaw=yaw, pitch=pitch, roll=roll,
                reason=f"Mat bi loa ({brightness:.1f} > {self.max_brightness})"
            )

        # 4. Kiem tra tu the 3D (3D Head Pose)
        if abs(yaw) > self.max_yaw:
            return QualityMetrics(
                passed=False, is_live_fft=is_live_fft, sharpness=sharpness,
                brightness=brightness, moire_score=moire_score,
                yaw=yaw, pitch=pitch, roll=roll,
                reason=f"Mat quay nghieng (Yaw: {yaw:+.1f})"
            )
        if abs(pitch) > self.max_pitch:
            return QualityMetrics(
                passed=False, is_live_fft=is_live_fft, sharpness=sharpness,
                brightness=brightness, moire_score=moire_score,
                yaw=yaw, pitch=pitch, roll=roll,
                reason=f"Mat cui/nguoc (Pitch: {pitch:+.1f})"
            )
        if abs(roll) > self.max_roll:
            return QualityMetrics(
                passed=False, is_live_fft=is_live_fft, sharpness=sharpness,
                brightness=brightness, moire_score=moire_score,
                yaw=yaw, pitch=pitch, roll=roll,
                reason=f"Dau nghieng (Roll: {roll:+.1f})"
            )

        reason = "Khuon mat dat chuan" if is_live_fft else f"Phat hien van Moire nhe ({moire_score:.2f})"
        return QualityMetrics(
            passed=True, is_live_fft=is_live_fft, sharpness=sharpness,
            brightness=brightness, moire_score=moire_score,
            yaw=yaw, pitch=pitch, roll=roll,
            reason=reason
        )
