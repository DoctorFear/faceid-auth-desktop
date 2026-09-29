"""
Dang ky khuon mat qua webcam (5 goc: thang, trai, phai, ngua, cui).

Cai dat:
    pip install opencv-python face_recognition numpy

Dung:
    python enroll_face.py <username>
    python enroll_face.py <username> --force      # ghi de user da ton tai, khong hoi
    python enroll_face.py <username> --camera 1

Phim: SPACE = chup ngay (van kiem tra chat luong) | D = bat/tat debug goc | Q/ESC = thoat

Quy uoc goc (nhu nhin trong guong):
    yaw   > 0 : quay sang PHAI     yaw   < 0 : quay sang TRAI
    pitch > 0 : ngang len          pitch < 0 : cui xuong
Neu thay nguoc chieu, doi YAW_SIGN / PITCH_SIGN trong Config (bam D de xem goc).
"""

from __future__ import annotations

import argparse
import logging
import os
import pickle
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import face_recognition
import numpy as np

log = logging.getLogger("enroll")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "faces.pickle")
WINDOW = "Smart Face Enrollment"


# --------------------------------------------------------------------------- #
# Cau hinh
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    cam_index: int = 0
    frame_w: int = 640
    frame_h: int = 480

    detect_scale: float = 0.5        # thu nho khi detect HOG cho nhanh
    ema_alpha: float = 0.5           # lam muot goc (1.0 = khong lam muot)
    hold_sec: float = 0.5            # giu dung tu the bao lau thi tu chup
    step_timeout_sec: float = 45.0   # qua thoi gian nay ma chua xong buoc -> huy
    angle_tolerance: float = 20.0    # do lech (do) de diem % ve 0

    yaw_sign: int = 1
    pitch_sign: int = -1             # solvePnP: pitch duong = cui -> dao dau

    min_face_px: int = 120           # mat nho hon (chieu cao box) -> qua xa
    min_sharpness: float = 40.0      # phuong sai Laplacian tren vung mat 200x200
    brightness_range: tuple = (60, 200)
    max_roll_deg: float = 25.0

    dup_threshold: float = 0.45      # trung user khac neu khoang cach < nguong
    consistency_threshold: float = 0.55  # 5 anh phai la cung 1 nguoi


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    guide: str
    yaw: tuple      # khoang goc yaw can dat (do)
    pitch: tuple    # khoang goc pitch can dat (do)
    relative: bool = True  # True: do so voi goc luc nhin thang


STEPS = [
    Step("THANG", "BUOC 1/5: NHIN THANG", "Nhin truc dien vao camera",
         yaw=(-12, 12), pitch=(-15, 15), relative=False),
    Step("TRAI", "BUOC 2/5: QUAY TRAI", "Quay mat sang TRAI <--",
         yaw=(-40, -15), pitch=(-15, 15)),
    Step("PHAI", "BUOC 3/5: QUAY PHAI", "Quay mat sang PHAI -->",
         yaw=(15, 40), pitch=(-15, 15)),
    Step("NGANG", "BUOC 4/5: NGANG LEN", "Hoi nang cam len [^]",
         yaw=(-15, 15), pitch=(10, 30)),
    Step("CUI", "BUOC 5/5: CUI XUONG", "Hoi cui cam xuong [v]",
         yaw=(-15, 15), pitch=(-30, -10)),
]


# --------------------------------------------------------------------------- #
# Luu tru
# --------------------------------------------------------------------------- #
def load_face_data(path: str = DATA_PATH) -> dict:
    """Doc DB. File hong -> sao luu ra .corrupt-* roi bao loi (KHONG tra {} de tranh ghi de mat du lieu)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        if not isinstance(data, dict):
            raise ValueError("DB khong phai dict")
        return data
    except Exception as exc:
        backup = f"{path}.corrupt-{int(time.time())}"
        shutil.copy2(path, backup)
        raise RuntimeError(f"Khong doc duoc {path} ({exc}). Da sao luu sang {backup}") from exc


def save_face_data(data: dict, path: str = DATA_PATH) -> None:
    """Ghi nguyen tu: ghi file tam roi os.replace; giu 1 ban .bak cua lan truoc."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")

    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# --------------------------------------------------------------------------- #
# Uoc luong goc dau (solvePnP)
# --------------------------------------------------------------------------- #
# He toa do camera: x sang phai, y xuong duoi, z ra xa -> tu the nhin thang ~ ma tran don vi.
MODEL_POINTS = np.array([
    (0.0, 0.0, 0.0),          # dau mui
    (0.0, 330.0, 65.0),       # cam
    (-225.0, -170.0, 135.0),  # khoe mat ngoai (ben trai anh)
    (225.0, -170.0, 135.0),   # khoe mat ngoai (ben phai anh)
    (-150.0, 150.0, 125.0),   # khoe mieng trai anh
    (150.0, 150.0, 125.0),    # khoe mieng phai anh
], dtype=np.float64)


def _wrap(angle: float) -> float:
    while angle > 90:
        angle -= 180
    while angle < -90:
        angle += 180
    return angle


def estimate_pose(image_points: np.ndarray, frame_w: int, frame_h: int, cfg: Config):
    """Tra ve (yaw, pitch, roll) theo do, hoac None neu solvePnP that bai."""
    focal = float(frame_w)
    cam = np.array([[focal, 0, frame_w / 2],
                    [0, focal, frame_h / 2],
                    [0, 0, 1]], dtype=np.float64)
    ok, rvec, _ = cv2.solvePnP(MODEL_POINTS, image_points, cam, np.zeros((4, 1)),
                               flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        return None
    rmat, _ = cv2.Rodrigues(rvec)
    euler = cv2.RQDecomp3x3(rmat)[0]  # (x, y, z) do
    pitch, yaw, roll = _wrap(euler[0]), _wrap(euler[1]), _wrap(euler[2])
    return cfg.yaw_sign * yaw, cfg.pitch_sign * pitch, roll


# --------------------------------------------------------------------------- #
# Phan tich frame
# --------------------------------------------------------------------------- #
@dataclass
class FaceInfo:
    box: tuple                    # (top, right, bottom, left) tren frame goc
    image_points: np.ndarray      # 6 diem moc tren frame goc
    problem: Optional[str]        # None neu chat luong dat


def _quality_problem(frame_bgr: np.ndarray, box: tuple, cfg: Config) -> Optional[str]:
    top, right, bottom, left = box
    if bottom - top < cfg.min_face_px:
        return "Mat qua xa, hay lai gan hon"
    h, w = frame_bgr.shape[:2]
    if top < 0 or left < 0 or bottom > h or right > w:
        return "Mat bi cat o mep khung hinh"

    roi = frame_bgr[max(top, 0):bottom, max(left, 0):right]
    if roi.size == 0:
        return "Khong doc duoc vung mat"
    gray = cv2.cvtColor(cv2.resize(roi, (200, 200)), cv2.COLOR_BGR2GRAY)

    mean = float(gray.mean())
    if mean < cfg.brightness_range[0]:
        return "Thieu sang"
    if mean > cfg.brightness_range[1]:
        return "Qua sang / choi"
    if cv2.Laplacian(gray, cv2.CV_64F).var() < cfg.min_sharpness:
        return "Anh bi mo, giu yen camera"
    return None


def analyze_frame(frame_bgr: np.ndarray, cfg: Config):
    """Tra ve (so_mat, FaceInfo | None). Detect tren anh thu nho, ket qua quy ve toa do goc."""
    s = cfg.detect_scale
    small = cv2.resize(frame_bgr, (0, 0), fx=s, fy=s)
    rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    boxes = face_recognition.face_locations(rgb_small, model="hog")
    if len(boxes) != 1:
        return len(boxes), None

    marks = face_recognition.face_landmarks(rgb_small, boxes)
    if not marks:
        return 1, None
    lm = marks[0]

    pts = np.array([
        lm["nose_tip"][2],
        lm["chin"][8],
        lm["left_eye"][0],
        lm["right_eye"][3],
        lm["top_lip"][0],
        lm["top_lip"][6],
    ], dtype=np.float64) / s

    box = tuple(int(v / s) for v in boxes[0])
    return 1, FaceInfo(box=box, image_points=pts, problem=_quality_problem(frame_bgr, box, cfg))


# --------------------------------------------------------------------------- #
# Diem % va giao dien
# --------------------------------------------------------------------------- #
def _axis_score(value: float, rng: tuple, tol: float) -> float:
    lo, hi = rng
    dist = lo - value if value < lo else value - hi if value > hi else 0.0
    return max(0.0, 1.0 - dist / tol)


def alignment(step: Step, yaw: float, pitch: float, tol: float):
    """Tra ve (phan_tram, dat_khoang_hay_chua)."""
    sy = _axis_score(yaw, step.yaw, tol)
    sp = _axis_score(pitch, step.pitch, tol)
    score = min(sy, sp)
    return int(round(score * 100)), score >= 1.0


def _text(img, text, org, scale, color, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_ui(img, step, percent, color, hold_frac, message, debug_text, flash, box_mirrored):
    h, w = img.shape[:2]

    if box_mirrored is not None:
        x1, y1, x2, y2 = box_mirrored
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

    cv2.rectangle(img, (0, 0), (w, 75), (25, 25, 25), -1)
    _text(img, step.title, (15, 30), 0.7, (0, 255, 255), 2)
    _text(img, step.guide, (15, 60), 0.55, (255, 255, 255))

    cv2.rectangle(img, (w - 130, 8), (w - 15, 67), (40, 40, 40), -1)
    _text(img, f"{percent}%", (w - 115, 52), 1.1, color, 3)

    cv2.rectangle(img, (0, 75), (w, 82), (40, 40, 40), -1)
    if hold_frac > 0:
        cv2.rectangle(img, (0, 75), (int(w * hold_frac), 82), (0, 255, 0), -1)

    if message:
        _text(img, message, (15, h - 60), 0.6, (0, 200, 255), 2)
    if debug_text:
        _text(img, debug_text, (15, h - 90), 0.5, (200, 200, 200))
    if flash:
        _text(img, "DA CHUP!", (w // 2 - 80, h // 2), 1.2, (0, 255, 0), 3)

    cv2.rectangle(img, (0, h - 35), (w, h), (20, 20, 20), -1)
    _text(img, "Dat dung tu the -> TU CHUP | SPACE: chup ngay | D: debug | Q: thoat",
          (12, h - 13), 0.47, (180, 180, 180))


# --------------------------------------------------------------------------- #
# Vong lap dang ky
# --------------------------------------------------------------------------- #
def open_camera(cfg: Config) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(cfg.cam_index)
    if not cap.isOpened():
        raise RuntimeError(f"Khong mo duoc camera (index {cfg.cam_index}).")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_h)
    return cap


def capture_encodings(cfg: Config) -> Optional[list]:
    """Chay 5 buoc chup. Tra ve list 5 encoding hoac None neu huy/loi."""
    cap = open_camera(cfg)
    encodings: list = []
    baseline = (0.0, 0.0)
    smooth: Optional[np.ndarray] = None
    hold_start: Optional[float] = None
    step_idx = 0
    step_start = time.time()
    flash_until = 0.0
    show_debug = False
    window_opened = False

    try:
        while step_idx < len(STEPS):
            ok, frame = cap.read()
            if not ok:
                log.error("Mat tin hieu camera.")
                return None

            now = time.time()
            if now - step_start > cfg.step_timeout_sec:
                log.error("Het thoi gian o %s, huy dang ky.", STEPS[step_idx].name)
                return None

            step = STEPS[step_idx]
            h, w = frame.shape[:2]
            n_faces, info = analyze_frame(frame, cfg)

            percent, in_range = 0, False
            color = (0, 0, 255)
            message = ""
            hold_frac = 0.0
            debug_text = ""
            box_mirrored = None
            can_capture = False

            if n_faces == 0:
                message = "Khong thay mat"
            elif n_faces > 1:
                message = "Chi 1 nguoi trong khung hinh"

            if info is not None:
                top, right, bottom, left = info.box
                box_mirrored = (w - right, top, w - left, bottom)  # hien thi guong

                pose = estimate_pose(info.image_points, w, h, cfg)
                if pose is None:
                    message = "Khong xac dinh duoc tu the"
                else:
                    raw = np.array(pose[:2])
                    smooth = raw if smooth is None else cfg.ema_alpha * raw + (1 - cfg.ema_alpha) * smooth
                    yaw, pitch = smooth - np.array(baseline) if step.relative else smooth
                    roll = pose[2]

                    percent, in_range = alignment(step, yaw, pitch, cfg.angle_tolerance)
                    color = (0, 255, 0) if in_range else (0, 200, 255) if percent >= 50 else (0, 0, 255)
                    debug_text = f"yaw={yaw:+.0f} pitch={pitch:+.0f} roll={roll:+.0f}" if show_debug else ""

                    if info.problem:
                        message = info.problem
                    elif abs(roll) > cfg.max_roll_deg:
                        message = "Dung nghieng dau sang mot ben"
                    else:
                        can_capture = True
            else:
                smooth = None

            # Giu dung tu the du lau moi tu chup
            auto_ready = False
            if can_capture and in_range:
                hold_start = hold_start or now
                hold_frac = min(1.0, (now - hold_start) / cfg.hold_sec)
                auto_ready = hold_frac >= 1.0
            else:
                hold_start = None

            display = cv2.flip(frame, 1)
            draw_ui(display, step, percent, color, hold_frac, message, debug_text,
                    now < flash_until, box_mirrored)
            cv2.imshow(WINDOW, display)
            window_opened = True

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                log.info("Nguoi dung huy.")
                return None
            if key == ord("d"):
                show_debug = not show_debug
            if window_opened and cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                log.info("Cua so bi dong.")
                return None

            manual = key == ord(" ") and can_capture
            if (auto_ready or manual) and info is not None:
                rgb_full = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # frame goc, KHONG lat
                enc = face_recognition.face_encodings(rgb_full, [info.box], num_jitters=1)
                if enc:
                    encodings.append(enc[0])
                    log.info("Chup %s (%d/%d) - do chuan %d%%",
                             step.name, step_idx + 1, len(STEPS), percent)
                    if step_idx == 0 and smooth is not None:
                        baseline = (float(smooth[0]), float(smooth[1]))
                    step_idx += 1
                    hold_start = None
                    smooth = None
                    step_start = time.time()
                    flash_until = step_start + 0.6
                    for _ in range(3):  # xa frame cu trong buffer
                        cap.grab()

        return encodings
    finally:
        cap.release()
        cv2.destroyAllWindows()


# --------------------------------------------------------------------------- #
# Kiem tra sau khi chup
# --------------------------------------------------------------------------- #
def check_consistency(encodings: list, threshold: float) -> float:
    dists = []
    for i in range(1, len(encodings)):
        dists.extend(face_recognition.face_distance(encodings[:i], encodings[i]))
    return float(np.mean(dists))


def find_duplicate(encodings: list, db: dict, username: str, threshold: float) -> Optional[str]:
    for other, other_encs in db.items():
        if other == username or not len(other_encs):
            continue
        best = min(float(face_recognition.face_distance(other_encs, e).min()) for e in encodings)
        if best < threshold:
            return other
    return None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def valid_username(name: str) -> bool:
    return bool(re.fullmatch(r"[\w.\- ]{1,50}", name))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Dang ky khuon mat 5 goc qua webcam")
    parser.add_argument("username", nargs="?", help="Ten nguoi dung")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Ghi de user da ton tai")
    args = parser.parse_args()

    username = (args.username or input("Username: ")).strip()
    if not valid_username(username):
        log.error("Username khong hop le (chi chu, so, _ . - va khoang trang, toi da 50 ky tu).")
        return 1

    cfg = Config(cam_index=args.camera)

    try:
        db = load_face_data()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if username in db and not args.force:
        answer = input(f"'{username}' da ton tai. Ghi de? [y/N]: ").strip().lower()
        if answer != "y":
            log.info("Huy.")
            return 0

    log.info("Bat dau dang ky: %s", username)
    try:
        encodings = capture_encodings(cfg)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    if encodings is None or len(encodings) != len(STEPS):
        log.error("Dang ky khong hoan tat, khong luu gi.")
        return 1

    spread = check_consistency(encodings, cfg.consistency_threshold)
    if spread > cfg.consistency_threshold:
        log.error("5 anh khong nhat quan (%.2f > %.2f). Thu lai voi anh sang, on dinh hon.",
                  spread, cfg.consistency_threshold)
        return 1

    dup = find_duplicate(encodings, db, username, cfg.dup_threshold)
    if dup:
        log.error("Khuon mat nay trung voi user '%s'. Khong luu.", dup)
        return 1

    db[username] = encodings
    save_face_data(db)
    log.info("THANH CONG: da luu %d goc cho '%s' vao %s", len(encodings), username, DATA_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
