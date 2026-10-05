import cv2
import numpy as np

# 5 diem moc 3D quy uoc tren khuon mat (Mat phai, Mat trai, Mui, Khoe mieng phai, Khoe mieng trai)
# He toa do: X sang phai, Y xuong duoi, Z ra xa
MODEL_POINTS_5 = np.array(
    [
        (-165.0, -170.0, 135.0),  # Mat phai (nhin tren anh)
        (165.0, -170.0, 135.0),   # Mat trai
        (0.0, 0.0, 0.0),          # Dau mui
        (-150.0, 150.0, 125.0),   # Khoe mieng phai
        (150.0, 150.0, 125.0),    # Khoe mieng trai
    ],
    dtype=np.float64,
)

def wrap_angle(angle: float) -> float:
    while angle > 90:
        angle -= 180
    while angle < -90:
        angle += 180
    return angle

class FaceQualityAssessor:
    """Bo kiem soat chat luong anh va uoc luong Head Pose tuong thich YuNet (5 landmarks)."""

    def __init__(
        self,
        min_size: int = 100,
        min_sharpness: float = 40.0,
        brightness_range: tuple[int, int] = (50, 210),
        max_roll: float = 25.0,
    ):
        self.min_size = min_size
        self.min_sharpness = min_sharpness
        self.brightness_range = brightness_range
        self.max_roll = max_roll

    def check_quality(self, frame_bgr: np.ndarray, box: tuple) -> str | None:
        """Kiem tra do net, anh sang va kich thuoc."""
        top, right, bottom, left = box
        h, w = frame_bgr.shape[:2]

        if bottom - top < self.min_size:
            return "Mat qua xa, hay tien lai gan"
        if top < 0 or left < 0 or bottom > h or right > w:
            return "Mat bi vuot ra ngoai vien camera"

        roi = frame_bgr[max(top, 0) : bottom, max(left, 0) : right]
        if roi.size == 0:
            return "Khong crop duoc vung mat"

        gray = cv2.cvtColor(cv2.resize(roi, (150, 150)), cv2.COLOR_BGR2GRAY)
        mean_brightness = float(gray.mean())

        if mean_brightness < self.brightness_range[0]:
            return "Anh qua toi"
        if mean_brightness > self.brightness_range[1]:
            return "Anh qua choi sang"

        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if sharpness < self.min_sharpness:
            return "Anh bi nhoe / mo, hay giu yen"

        return None

    def estimate_pose(
        self,
        landmarks: np.ndarray,
        frame_w: int,
        frame_h: int,
        yaw_sign: int = 1,
        pitch_sign: int = -1,
    ) -> tuple[float, float, float] | None:
        """Uoc luong pose (yaw, pitch, roll) tu 5 landmarks su dung cv2.SOLVEPNP_EPNP."""
        if landmarks.shape != (5, 2):
            return None

        focal = float(frame_w)
        cam_matrix = np.array(
            [[focal, 0, frame_w / 2.0], [0, focal, frame_h / 2.0], [0, 0, 1.0]],
            dtype=np.float64,
        )
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        image_pts = np.ascontiguousarray(landmarks, dtype=np.float64)
        
        # Su dung SOLVEPNP_EPNP cho bo du lieu < 6 diem
        ok, rvec, tvec = cv2.solvePnP(
            MODEL_POINTS_5,
            image_pts,
            cam_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_EPNP,
        )
        if not ok:
            return None

        # Tinh chinh bang Levenberg-Marquardt iterative sau EPNP
        ok, rvec, _ = cv2.solvePnP(
            MODEL_POINTS_5,
            image_pts,
            cam_matrix,
            dist_coeffs,
            rvec=rvec,
            tvec=tvec,
            useExtrinsicGuess=True,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok:
            return None

        rmat, _ = cv2.Rodrigues(rvec)
        euler = cv2.RQDecomp3x3(rmat)[0]
        pitch, yaw, roll = wrap_angle(euler[0]), wrap_angle(euler[1]), wrap_angle(euler[2])
        return yaw_sign * yaw, pitch_sign * pitch, roll
