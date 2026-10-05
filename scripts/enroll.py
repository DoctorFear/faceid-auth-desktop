from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

try:  # chi co tren Linux/macOS
    import pwd
except ImportError:  # pragma: no cover
    pwd = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.embedder import ArcFaceEmbedder

log = logging.getLogger("enroll")
DEFAULT_EMBEDDINGS_DIR = os.environ.get(
    "FACEID_EMBEDDINGS_DIR", os.path.join(BASE_DIR, "data", "embeddings")
)
WINDOW = "FaceID Auto Enrollment (Hands-Free)"
USERNAME_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


@dataclass(frozen=True)
class Config:
    cam_index: int = 0
    frame_w: int = 640
    frame_h: int = 480
    hold_sec: float = 0.6            # giu on dinh 0.6s moi chup (tranh chup luc dang quay dau)
    cooldown_sec: float = 1.0        # nghi sau moi lan chup, tranh chup lien tiep bang cung tu the
    step_timeout_sec: float = 60.0
    angle_tolerance: float = 30.0
    in_range_score: float = 0.9      # 0.9 ~ lech toi da 3 do ngoai khoang muc tieu
    yaw_sign: int = 1               # Dong bo voi huong guong
    pitch_sign: int = -1
    max_roll_deg: float = 35.0
    min_face_px: int = 80
    edge_margin_frac: float = 0.15   # cho phep box lan ra ngoai khung toi da 15% kich thuoc mat
    first_step_max_yaw: float = 15.0   # buoc 1 chi can nhin "gan thang" (chua co moc goc)
    first_step_max_pitch: float = 30.0
    max_read_failures: int = 60
    debug: bool = False


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    guide: str
    target_yaw: tuple[float, float]
    target_pitch: tuple[float, float]


# Goc muc tieu la goc TUONG DOI so voi tu the nhin thang o buoc 1 (da hieu chuan theo camera)
STEPS = [
    Step("THANG", "BUOC 1/5: NHIN THANG", "Nhin thang truc dien vao camera",
         target_yaw=(-12, 12), target_pitch=(-12, 12)),
    Step("TRAI", "BUOC 2/5: QUAY TRAI", "Quay nhe mat sang ben TRAI cua ban <--",
         target_yaw=(-40, -8), target_pitch=(-25, 25)),
    Step("PHAI", "BUOC 3/5: QUAY PHAI", "Quay nhe mat sang ben PHAI cua ban -->",
         target_yaw=(8, 40), target_pitch=(-25, 25)),
    Step("NGUOC", "BUOC 4/5: NGUOC LEN", "Hoi nang nhe cam / mat len [^]",
         target_yaw=(-25, 25), target_pitch=(6, 35)),
    Step("CUI", "BUOC 5/5: CUI XUONG", "Hoi cui nhe cam / dau xuong [v]",
         target_yaw=(-25, 25), target_pitch=(-35, -6)),
]


def save_user_embeddings_atomic(username: str, encodings: list[np.ndarray], out_dir: str,
                                append: bool = False, max_vectors: int = 30) -> tuple[str, int]:
    """Ghi atomic, quyen 0600. Tra ve (duong_dan, so_vector)."""
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    target_path = os.path.join(out_dir, f"{username}.npy")
    arr = np.asarray(encodings, dtype=np.float32)

    if append and os.path.exists(target_path):
        old = np.load(target_path, allow_pickle=False)  # khong bao gio nap pickle
        if old.ndim == 2 and old.shape[1] == arr.shape[1]:
            arr = np.concatenate([old.astype(np.float32), arr])[-max_vectors:]
        else:
            log.warning("File cu co dinh dang/so chieu khac, se ghi de.")

    fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        # QUAN TRONG: truyen file object. np.save(<duong dan khong co .npy>) se tu them
        # ".npy" vao ten file, khien du lieu nam o file khac con file dich bi rong.
        with os.fdopen(fd, "wb") as f:
            np.save(f, arr)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return target_path, len(arr)


def calc_score(val: float, target_range: tuple[float, float], tol: float) -> float:
    lo, hi = target_range
    if lo <= val <= hi:
        return 1.0
    dist = lo - val if val < lo else val - hi
    return max(0.0, 1.0 - dist / tol)


def clamp_box(box: tuple, w: int, h: int) -> tuple[int, int, int, int]:
    top, right, bottom, left = box
    return (max(0, int(top)), min(w, int(right)), min(h, int(bottom)), max(0, int(left)))


def check_face_basic(frame_bgr: np.ndarray, box: tuple, cfg: Config) -> str | None:
    """Mat khong qua nho; cho phep box lan nhe ra ngoai khung (YuNet hay tra box vuot bien)."""
    top, right, bottom, left = box
    h, w = frame_bgr.shape[:2]
    bw, bh = right - left, bottom - top
    if bh < cfg.min_face_px:
        return "Mat qua xa, lai gan hon"
    mx, my = bw * cfg.edge_margin_frac, bh * cfg.edge_margin_frac
    if left < -mx or top < -my or right > w + mx or bottom > h + my:
        return "Mat bi cat o mep, lui ra giua khung hinh"
    return None


def sharpness(frame_bgr: np.ndarray, box: tuple) -> float:
    h, w = frame_bgr.shape[:2]
    t, r, b, l = clamp_box(box, w, h)
    if b - t < 8 or r - l < 8:
        return 0.0
    gray = cv2.cvtColor(frame_bgr[t:b, l:r], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def open_camera(cfg: Config) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(cfg.cam_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(cfg.cam_index)
    if not cap.isOpened():
        raise RuntimeError(f"Khong the mo camera tai index {cfg.cam_index}")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_h)
    time.sleep(0.5)
    for _ in range(5):  # bo vai frame dau (tu dong phoi sang)
        cap.read()
    return cap


def draw_hud(img, step: Step, yaw: float, pitch: float, percent: int, in_range: bool,
             hold_frac: float, warning: str, flash: bool, box_mirrored: tuple | None):
    h, w = img.shape[:2]
    status_color = (0, 255, 0) if in_range else (0, 200, 255) if percent >= 60 else (0, 0, 255)

    if box_mirrored is not None:
        x1, y1, x2, y2 = (int(v) for v in box_mirrored)
        cv2.rectangle(img, (x1, y1), (x2, y2), status_color, 2)

    # Header
    cv2.rectangle(img, (0, 0), (w, 75), (20, 20, 20), -1)
    cv2.putText(img, step.title, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(img, step.guide, (15, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    # Hop phan tram
    cv2.rectangle(img, (w - 120, 8), (w - 12, 68), (35, 35, 35), -1)
    cv2.putText(img, f"{percent}%", (w - 108, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.0, status_color, 3, cv2.LINE_AA)

    # Thanh tien trinh tu dong chup
    cv2.rectangle(img, (0, 75), (w, 86), (40, 40, 40), -1)
    if hold_frac > 0:
        fill_w = int(w * hold_frac)
        cv2.rectangle(img, (0, 75), (fill_w, 86), (0, 255, 0), -1)

    # Hien thi goc (tuong doi so voi tu the nhin thang)
    target_info = f"Muc tieu: Yaw[{step.target_yaw[0]}..{step.target_yaw[1]}]  Pitch[{step.target_pitch[0]}..{step.target_pitch[1]}]"
    actual_info = f"Hien tai: Yaw = {yaw:+.1f}   Pitch = {pitch:+.1f}"
    cv2.putText(img, target_info, (15, h - 65), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
    cv2.putText(img, actual_info, (15, h - 42), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)

    if warning:
        cv2.putText(img, warning, (15, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 100, 255), 2, cv2.LINE_AA)
    elif in_range:
        cv2.putText(img, "DANG GIU TU THE... TU DONG CHUP!", (15, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)

    if flash:
        cv2.putText(img, "DA CHUP!", (w // 2 - 90, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 255, 0), 3, cv2.LINE_AA)


def capture_encodings(cfg: Config) -> Optional[list[np.ndarray]]:
    from core.fiqa import FaceQualityAssessor

    detector = YuNetDetector()
    embedder = ArcFaceEmbedder()
    assessor = FaceQualityAssessor(max_roll=cfg.max_roll_deg)

    cap = open_camera(cfg)

    encodings: list[np.ndarray] = []
    step_idx = 0
    step_start = time.time()
    hold_start = None
    cooldown_until = 0.0
    flash_until = 0.0
    smooth: tuple[float, float] | None = None   # EMA cua goc tho (yaw, pitch)
    baseline = (0.0, 0.0)                       # goc tho khi nhin thang (de hieu chuan camera)
    best = None                                 # frame net nhat trong luc giu tu the
    read_fail = 0
    last_debug = 0.0

    try:
        while step_idx < len(STEPS):
            now = time.time()
            if now - step_start > cfg.step_timeout_sec:
                log.error("Het thoi gian tai buoc %s.", STEPS[step_idx].name)
                return None

            ok, frame = cap.read()
            if not ok or frame is None:
                read_fail += 1
                if read_fail > cfg.max_read_failures:
                    log.error("Camera khong tra ve khung hinh.")
                    return None
                time.sleep(0.02)
                continue
            read_fail = 0

            step = STEPS[step_idx]
            h, w = frame.shape[:2]
            detections = detector.detect(frame)
            n_faces = len(detections)

            percent, in_range = 0, False
            warning = ""
            hold_frac = 0.0
            box_mirrored = None
            can_capture = False
            active_box = None
            rel_yaw = rel_pitch = 0.0

            if n_faces == 0:
                warning = "Khong tim thay mat"
                smooth = None
            elif n_faces > 1:
                warning = "Chi 1 nguoi trong khung hinh"
            else:
                box, landmarks, _ = detections[0]
                top, right, bottom, left = box
                box_mirrored = (w - right, top, w - left, bottom)

                pose = assessor.estimate_pose(landmarks, w, h, cfg.yaw_sign, cfg.pitch_sign)
                if pose is None:
                    warning = "Dang do goc..."
                else:
                    raw_yaw, raw_pitch, roll = pose
                    if smooth is None:
                        smooth = (raw_yaw, raw_pitch)
                    else:
                        smooth = (0.5 * raw_yaw + 0.5 * smooth[0], 0.5 * raw_pitch + 0.5 * smooth[1])
                    rel_yaw = smooth[0] - baseline[0]
                    rel_pitch = smooth[1] - baseline[1]

                    if cfg.debug and now - last_debug > 0.5:
                        last_debug = now
                        log.info("[%s] raw yaw=%+.1f pitch=%+.1f roll=%+.1f | rel yaw=%+.1f pitch=%+.1f | box=%s",
                                 step.name, raw_yaw, raw_pitch, roll, rel_yaw, rel_pitch, tuple(int(v) for v in box))

                    if step_idx == 0:
                        # Buoc 1: chua co moc goc, chi can nhin gan thang; goc nay se thanh moc 0
                        ok_pose = (abs(smooth[0]) <= cfg.first_step_max_yaw
                                   and abs(smooth[1]) <= cfg.first_step_max_pitch)
                        percent = 100 if ok_pose else 0
                        in_range = ok_pose
                    else:
                        score = min(calc_score(rel_yaw, step.target_yaw, cfg.angle_tolerance),
                                    calc_score(rel_pitch, step.target_pitch, cfg.angle_tolerance))
                        percent = int(round(score * 100))
                        in_range = score >= cfg.in_range_score

                    basic_err = check_face_basic(frame, box, cfg)
                    if basic_err:
                        warning = basic_err
                    elif abs(roll) > cfg.max_roll_deg:
                        warning = "Dung nghieng dau sang hai ben"
                    elif now < cooldown_until:
                        warning = "Vua chup xong, chuan bi buoc tiep theo..."
                    else:
                        can_capture = True
                        active_box = box

            # Giu on dinh du lau moi chup; chon frame net nhat trong luc giu
            auto_ready = False
            if can_capture and in_range and active_box is not None and smooth is not None:
                if hold_start is None:
                    hold_start = now
                    best = None
                hold_frac = min(1.0, (now - hold_start) / cfg.hold_sec)
                sharp = sharpness(frame, active_box)
                if best is None or sharp > best[0]:
                    best = (sharp, frame.copy(), active_box, smooth)
                auto_ready = hold_frac >= 1.0
            else:
                hold_start = None
                best = None

            display = cv2.flip(frame, 1)
            draw_hud(display, step, rel_yaw, rel_pitch, percent, in_range, hold_frac,
                     warning, now < flash_until, box_mirrored)
            cv2.imshow(WINDOW, display)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return None

            if auto_ready and best is not None:
                _, best_frame, best_box, best_smooth = best
                bh, bw = best_frame.shape[:2]
                emb = embedder.extract(best_frame, clamp_box(best_box, bw, bh))
                encodings.append(emb)
                if step_idx == 0:
                    baseline = best_smooth
                    log.info("Moc goc nhin thang: yaw=%+.1f pitch=%+.1f", *baseline)
                log.info("[OK] TU DONG CHUP: %s (%d/%d)", step.name, step_idx + 1, len(STEPS))

                step_idx += 1
                hold_start = None
                best = None
                step_start = time.time()
                flash_until = step_start + 0.4
                cooldown_until = step_start + cfg.cooldown_sec
                for _ in range(4):  # Xa buffer
                    cap.grab()

        return encodings
    finally:
        cap.release()
        cv2.destroyAllWindows()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Auto Enrollment")
    parser.add_argument("username", nargs="?", help="Username")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--data-dir", default=DEFAULT_EMBEDDINGS_DIR,
                        help="Thu muc luu embedding (mac dinh: $FACEID_EMBEDDINGS_DIR hoac data/embeddings)")
    parser.add_argument("--append", action="store_true",
                        help="Them vao embedding da co (dang ky them dieu kien: kinh, anh sang...)")
    parser.add_argument("--max-vectors", type=int, default=30)
    parser.add_argument("--debug", action="store_true", help="In goc tho/box de chan doan")
    args = parser.parse_args()

    username = (args.username or input("Username: ")).strip()
    if not USERNAME_RE.fullmatch(username):
        log.error("Username khong hop le.")
        return 1
    if pwd is not None:
        try:
            pwd.getpwnam(username)
        except KeyError:
            log.warning("'%s' khong phai tai khoan he thong; PAM se khong khop duoc.", username)

    cfg = Config(cam_index=args.camera, debug=args.debug)
    log.info("Bat dau tu dong dang ky cho: %s", username)

    encodings = capture_encodings(cfg)
    if encodings is None or len(encodings) != len(STEPS):
        log.error("Chua hoan tat dang ky.")
        return 1

    path, n = save_user_embeddings_atomic(username, encodings, args.data_dir,
                                          append=args.append, max_vectors=args.max_vectors)
    log.info("[OK] THANH CONG: file %s hien co %d vector ArcFace", path, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())