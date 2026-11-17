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
from PIL import Image, ImageDraw, ImageFont

try:
    import pwd
except ImportError:
    pwd = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.embedder import ArcFaceEmbedder
from core.constants import MIN_FACE_SIZE_PX, POSE_YAW_SIGN, POSE_PITCH_SIGN, FIQA_MAX_YAW, FIQA_MAX_PITCH

log = logging.getLogger("enroll")
DEFAULT_EMBEDDINGS_DIR = os.environ.get(
    "FACEID_EMBEDDINGS_DIR", os.path.join(BASE_DIR, "data", "embeddings")
)
WINDOW = "FaceID Auto Enrollment (Hands-Free)"
USERNAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]{0,31}")


@dataclass(frozen=True)
class Config:
    cam_index: int = 0
    frame_w: int = 640
    frame_h: int = 480
    hold_sec: float = 0.65           # Giam xuong 0.65s de do moi co, chup nhanh gon hon
    cooldown_sec: float = 1.0        # Nghi 1s giua cac lan chup
    step_timeout_sec: float = 60.0
    angle_tolerance: float = 20.0    # Dung sai 20 do giup de dat phan tram chup hon
    in_range_score: float = 0.90     # Nguong 90% de vao vung chup tu dong
    yaw_sign: int = POSE_YAW_SIGN    # Huong quay dong bo theo hien thi guong
    pitch_sign: int = POSE_PITCH_SIGN
    max_roll_deg: float = 25.0       # Cho phep do nghieng dau thoai mai hon (25 do)
    min_face_px: int = MIN_FACE_SIZE_PX
    edge_margin_frac: float = 0.15
    first_step_max_yaw: float = FIQA_MAX_YAW
    first_step_max_pitch: float = FIQA_MAX_PITCH
    max_read_failures: int = 60
    debug: bool = False


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    guide: str
    target_yaw: tuple[float, float]
    target_pitch: tuple[float, float]


# Cac moc goc da duoc ha do cao cuc doan xuong muc nguoc/cui tu nhien (Ho tro tieng Viet co dau chuan xac)
STEPS = [
    Step("THANG", "BƯỚC 1/5: NHÌN THẲNG", "Vui lòng nhìn thẳng trực diện vào ống kính camera",
         target_yaw=(-8, 8), target_pitch=(-8, 8)),
    Step("TRAI", "BƯỚC 2/5: QUAY TRÁI", "Nghiêng nhẹ khuôn mặt sang bên TRÁI",
         target_yaw=(-35, -12), target_pitch=(-12, 12)),
    Step("PHAI", "BƯỚC 3/5: QUAY PHẢI", "Nghiêng nhẹ khuôn mặt sang bên PHẢI",
         target_yaw=(12, 35), target_pitch=(-12, 12)),
    Step("NGUOC", "BƯỚC 4/5: NGẨNG ĐẦU", "Hơi nâng nhẹ cằm lên phía trên",
         target_yaw=(-14, 14), target_pitch=(6, 22)),
    Step("CUI", "BƯỚC 5/5: CÚI ĐẦU", "Hơi cúi nhẹ cằm xuống phía dưới",
         target_yaw=(-14, 14), target_pitch=(-22, -6)),
]

# chống in nhiều hơn 5 vector trong 1 khuôn mặt 1 file
def consolidate_templates_to_k_prototypes(vectors: np.ndarray, k: int = 5) -> np.ndarray:
    """Gom cum va nen tap embedding ve dung k=5 vector dai dien (Spherical K-Means) de triet tieu FAR inflation."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    vecs = vectors / norms
    if len(vecs) <= k:
        return vecs.astype(np.float32)

    rng = np.random.default_rng(42)
    centers = [vecs[rng.choice(len(vecs))]]
    for _ in range(1, k):
        sims = np.max([np.dot(vecs, c) for c in centers], axis=0)
        dists = np.maximum(0.0, 1.0 - sims)
        probs = dists / (np.sum(dists) + 1e-9)
        centers.append(vecs[rng.choice(len(vecs), p=probs)])
    centers = np.array(centers)

    for _ in range(20):
        assignments = np.argmax(np.dot(vecs, centers.T), axis=1)
        new_centers = []
        for j in range(k):
            members = vecs[assignments == j]
            if len(members) > 0:
                c = np.mean(members, axis=0)
                new_centers.append(c / (np.linalg.norm(c) + 1e-9))
            else:
                new_centers.append(centers[j])
        new_centers = np.array(new_centers, dtype=np.float32)
        if np.allclose(centers, new_centers, atol=1e-4):
            break
        centers = new_centers
    return centers.astype(np.float32)

# chống nạp ảnh dang dở & chống lưu file embedding dạng pkl
def save_user_embeddings_atomic(username: str, encodings: list[np.ndarray], out_dir: str,
                                append: bool = False, max_vectors: int = 5) -> tuple[str, int]:
    """Ghi file an toan atomic, set quyen 0600, allow_pickle=False.
    Su dung co che 5 Pose Prototypes va Spherical Centroid de triet tieu FAR Inflation."""
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    target_path = os.path.join(out_dir, f"{username}.npy")
    new_arr = np.asarray(encodings, dtype=np.float32)

    new_norms = np.linalg.norm(new_arr, axis=1, keepdims=True) + 1e-9
    new_arr = new_arr / new_norms

    if append and os.path.exists(target_path):
        try:
            old = np.load(target_path, allow_pickle=False).astype(np.float32)
            if old.ndim == 2 and old.shape[1] == new_arr.shape[1]:
                if len(old) == len(new_arr) == 5:
                    combined = 0.5 * old + 0.5 * new_arr
                    c_norms = np.linalg.norm(combined, axis=1, keepdims=True) + 1e-9
                    arr = (combined / c_norms).astype(np.float32)
                    log.info("Cap nhat 5 Pose Prototypes (Spherical Average) cho user: %s", username)
                else:
                    all_vecs = np.concatenate([old, new_arr], axis=0)
                    arr = consolidate_templates_to_k_prototypes(all_vecs, k=max_vectors)
                    log.info("Gom cum %d vectors ve %d prototypes dai dien", len(all_vecs), len(arr))
            else:
                log.warning("File cu co dinh dang/so chieu khac, se ghi de.")
                arr = new_arr[:max_vectors]
        except Exception as e:
            log.warning("Loi khi doc file cu: %s, se ghi de.", e)
            arr = new_arr[:max_vectors]
    else:
        arr = new_arr[:max_vectors]

    fd, tmp_path = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
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


def load_user_templates(username: str, emb_dir: str = DEFAULT_EMBEDDINGS_DIR) -> np.ndarray | None:
    """Nạp tập vector template của một user từ data/embeddings/<username>.npy.
    Nếu file có nhiều hơn 5 vector (từ phiên bản cũ), tự động nén về 5 Pose Prototypes."""
    user_file = os.path.join(emb_dir, f"{username}.npy")
    if not os.path.exists(user_file):
        return None
    try:
        arr = np.load(user_file, allow_pickle=False).astype(np.float32)
        if arr.ndim == 2 and arr.shape[1] == 512:
            if len(arr) > 5:
                arr = consolidate_templates_to_k_prototypes(arr, k=5)
            norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
            return (arr / norms).astype(np.float32)
        return arr
    except Exception as e:
        log.warning("Khong the doc template cua user '%s': %s", username, e)
        return None


def load_all_enrolled_templates(emb_dir: str = DEFAULT_EMBEDDINGS_DIR) -> list[tuple[str, np.ndarray]]:
    """Nap tat ca cac khuon mat hop le da dang ky trong thu muc embeddings.
    Tra ve danh sach cac tuple: (ten_khuon_mat, mang_5_vector)."""
    results = []
    if not os.path.exists(emb_dir):
        return results
    for fname in os.listdir(emb_dir):
        if fname.endswith(".npy"):
            uname = fname[:-4]
            t = load_user_templates(uname, emb_dir=emb_dir)
            if t is not None and len(t) > 0:
                results.append((uname, t))
    return results


# Alias load_user_template = load_user_templates
load_user_template = load_user_templates


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
    top, right, bottom, left = box
    h, w = frame_bgr.shape[:2]
    bw, bh = right - left, bottom - top
    if bh < cfg.min_face_px:
        return "Khuôn mặt quá xa, vui lòng tiến lại gần hơn"
    mx, my = bw * cfg.edge_margin_frac, bh * cfg.edge_margin_frac
    if left < -mx or top < -my or right > w + mx or bottom > h + my:
        return "Khuôn mặt bị lệch mép, vui lòng đưa vào trung tâm"
    return None


def sharpness(frame_bgr: np.ndarray, box: tuple) -> float:
    h, w = frame_bgr.shape[:2]
    t, r, b, l = clamp_box(box, w, h)
    if b - t < 8 or r - l < 8:
        return 0.0
    gray = cv2.cvtColor(frame_bgr[t:b, l:r], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def open_camera(cfg: Config) -> cv2.VideoCapture:
    cam_backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2
    cap = cv2.VideoCapture(cfg.cam_index, cam_backend)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(cfg.cam_index)
    if not cap.isOpened():
        raise RuntimeError(f"Khong the mo camera tai index {cfg.cam_index}")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.frame_w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.frame_h)
    time.sleep(0.5)
    for _ in range(5):
        cap.read()
    return cap

# Ham ve text Unicode tieng Viet co dau sac net bang PIL voi Font Segoe UI (Font chuan hien dai cua Windows)
_FONT_CACHE = {}
def get_font(size: int = 18, bold: bool = False):
    font_key = (size, bold)
    if font_key not in _FONT_CACHE:
        font_path = "C:\\Windows\\Fonts\\segoeuib.ttf" if bold else "C:\\Windows\\Fonts\\segoeui.ttf"
        if not os.path.exists(font_path):
            font_path = "C:\\Windows\\Fonts\\arial.ttf"
        try:
            _FONT_CACHE[font_key] = ImageFont.truetype(font_path, size)
        except Exception:
            _FONT_CACHE[font_key] = ImageFont.load_default()
    return _FONT_CACHE[font_key]

def draw_vn_text(img: np.ndarray, text: str, pos: tuple[int, int],
                 size: int = 18, color_bgr: tuple[int, int, int] = (255, 255, 255), bold: bool = False) -> None:
    font = get_font(size, bold)
    color_rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
    
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    draw.text(pos, text, font=font, fill=color_rgb)
    
    res_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    img[:] = res_bgr[:]

def draw_vn_text_centered(img: np.ndarray, text: str, center_rect: tuple[int, int, int, int],
                          size: int = 18, color_bgr: tuple[int, int, int] = (255, 255, 255), bold: bool = False) -> None:
    """Can giua chinh xac tuyet doi ca chieu ngang (X) va chieu doc (Y) trong khung chu nhat."""
    font = get_font(size, bold)
    rx1, ry1, rx2, ry2 = center_rect
    rw, rh = rx2 - rx1, ry2 - ry1

    # Do kich thuoc bounding box cua doan chu bang PIL bbox
    bbox = font.getbbox(text)  # (left, top, right, bottom)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    tx = int(rx1 + (rw - tw) / 2.0 - bbox[0])
    ty = int(ry1 + (rh - th) / 2.0 - bbox[1])

    color_rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    draw.text((tx, ty), text, font=font, fill=color_rgb)

    res_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    img[:] = res_bgr[:]

def draw_rounded_card(img: np.ndarray, pt1: tuple[int, int], pt2: tuple[int, int],
                      bg_bgr: tuple[int, int, int], border_bgr: tuple[int, int, int] | None = None,
                      radius: int = 12) -> None:
    """Ve hop Card bo tron 4 goc phong cach UI hien dai bang PIL."""
    x1, y1 = pt1
    x2, y2 = pt2
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    
    fill_rgb = (bg_bgr[2], bg_bgr[1], bg_bgr[0])
    outline_rgb = (border_bgr[2], border_bgr[1], border_bgr[0]) if border_bgr else None
    
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill_rgb, outline=outline_rgb, width=1)
    
    res_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    img[:] = res_bgr[:]

# Canvas layout: Giu nguyen video camera 640x480 o giua, them 2 banner rieng:
# Top Bar: 76px (chua tieu de, chi dan hanh dong, phan tram %)
# Bottom Bar: 64px (chua thanh tien do giu goc, canh bao)
# Tong kich thuoc: 640 x (76 + 480 + 64) = 640 x 620
HEADER_HEIGHT = 76
CAM_HEIGHT = 480
CAM_WIDTH = 640
FOOTER_HEIGHT = 64
TOTAL_WIDTH = CAM_WIDTH
TOTAL_HEIGHT = HEADER_HEIGHT + CAM_HEIGHT + FOOTER_HEIGHT  # 620px

def make_enroll_canvas(cam_frame_bgr: np.ndarray) -> np.ndarray:
    canvas = np.zeros((TOTAL_HEIGHT, TOTAL_WIDTH, 3), dtype=np.uint8)
    # Nen Background toan man hinh: Mau sang tinh te #f0f7ff giong het Modern GUI (BGR: 255, 247, 240)
    canvas[:, :] = (255, 247, 240)
    # Dat tron ven camera vao giua (khong bi che 1 pixel nao!)
    canvas[HEADER_HEIGHT:HEADER_HEIGHT + CAM_HEIGHT, :CAM_WIDTH] = cam_frame_bgr
    return canvas

# ve man hinh cho (Welcome / Ready Screen)
def draw_welcome_hud(canvas: np.ndarray, username: str, box_mirrored: tuple | None, warning: str = ""):
    if box_mirrored is not None:
        x1, y1, x2, y2 = (int(v) for v in box_mirrored)
        cy1, cy2 = y1 + HEADER_HEIGHT, y2 + HEADER_HEIGHT
        cv2.rectangle(canvas, (x1, cy1), (x2, cy2), (235, 99, 37), 2)  # Xanh Blue Modern GUI
        clen = 16
        for bx, by, dx, dy in [(x1, cy1, 1, 1), (x2, cy1, -1, 1), (x1, cy2, 1, -1), (x2, cy2, -1, -1)]:
            cv2.line(canvas, (bx, by), (bx + dx * clen, by), (235, 99, 37), 3)
            cv2.line(canvas, (bx, by), (bx, by + dy * clen), (235, 99, 37), 3)

    # 1. Top Card Header: Nen trang tinh, vien xanh nhe giong Modern GUI
    draw_rounded_card(canvas, (12, 10), (TOTAL_WIDTH - 12, HEADER_HEIGHT - 8),
                      bg_bgr=(255, 255, 255), border_bgr=(254, 242, 224), radius=12)
    name_display = username.upper() if username else "NGƯỜI DÙNG"
    draw_vn_text(canvas, "FACEID HYBRID  •  ĐĂNG KÝ KHUÔN MẶT", (26, 17), size=16, color_bgr=(235, 99, 37), bold=True)
    draw_vn_text(canvas, f"Tài khoản: {name_display}   |   Quy trình lấy mẫu: 5 góc cử động", (26, 42), size=13, color_bgr=(100, 116, 139))

    # 2. Bottom Card Footer (Can giua toan bo thong diep)
    fy = HEADER_HEIGHT + CAM_HEIGHT
    card_rect = (12, fy + 8, TOTAL_WIDTH - 12, TOTAL_HEIGHT - 10)
    if warning:
        draw_rounded_card(canvas, (12, fy + 8), (TOTAL_WIDTH - 12, TOTAL_HEIGHT - 10),
                          bg_bgr=(242, 242, 254), border_bgr=(180, 180, 244), radius=12)
        draw_vn_text_centered(canvas, warning.upper(), card_rect, size=15, color_bgr=(60, 60, 220), bold=True)
    else:
        draw_rounded_card(canvas, (12, fy + 8), (TOTAL_WIDTH - 12, TOTAL_HEIGHT - 10),
                          bg_bgr=(255, 255, 255), border_bgr=(254, 242, 224), radius=12)
        draw_vn_text_centered(canvas, "NHẤN PHÍM [ SPACE ] ĐỂ BẮT ĐẦU CHỤP", card_rect, size=16, color_bgr=(105, 185, 16), bold=True)

# ve thong bao dieu chinh khi chup hinh (Top & Bottom Bar - Dong bo Style Modern GUI)
def draw_hud(canvas: np.ndarray, step: Step, percent: int, in_range: bool,
             hold_frac: float, warning: str, flash: bool, box_mirrored: tuple | None,
             extra_boxes_mirrored: list | None = None):
    # Palette mau giong Modern GUI Vibrant:
    # 100%: Xanh la Emerald (105, 185, 16), >=60%: Xanh duong Sky (235, 99, 37), <60%: Cam/Do Rose (60, 60, 230)
    status_bgr = (105, 185, 16) if in_range else ((235, 99, 37) if percent >= 60 else (60, 60, 230))

    # 1. Ve khung khuon mat tren vung camera
    if extra_boxes_mirrored:
        for eb in extra_boxes_mirrored:
            ex1, ey1, ex2, ey2 = (int(v) for v in eb)
            cy1, cy2 = ey1 + HEADER_HEIGHT, ey2 + HEADER_HEIGHT
            cv2.rectangle(canvas, (ex1, cy1), (ex2, cy2), (60, 60, 230), 2)
            draw_vn_text(canvas, "NGƯỜI PHỤ", (ex1, max(HEADER_HEIGHT + 8, cy1 - 22)), size=12, color_bgr=(60, 60, 230), bold=True)

    if box_mirrored is not None:
        x1, y1, x2, y2 = (int(v) for v in box_mirrored)
        cy1, cy2 = y1 + HEADER_HEIGHT, y2 + HEADER_HEIGHT
        cv2.rectangle(canvas, (x1, cy1), (x2, cy2), status_bgr, 2)
        clen = 16
        for bx, by, dx, dy in [(x1, cy1, 1, 1), (x2, cy1, -1, 1), (x1, cy2, 1, -1), (x2, cy2, -1, -1)]:
            cv2.line(canvas, (bx, by), (bx + dx * clen, by), status_bgr, 3)
            cv2.line(canvas, (bx, by), (bx, by + dy * clen), status_bgr, 3)

    if flash:
        cam_roi = canvas[HEADER_HEIGHT:HEADER_HEIGHT + CAM_HEIGHT, :CAM_WIDTH]
        flash_overlay = np.full_like(cam_roi, 255)
        cv2.addWeighted(flash_overlay, 0.40, cam_roi, 0.60, 0, cam_roi)
        draw_rounded_card(canvas, (CAM_WIDTH // 2 - 140, HEADER_HEIGHT + CAM_HEIGHT // 2 - 28),
                          (CAM_WIDTH // 2 + 140, HEADER_HEIGHT + CAM_HEIGHT // 2 + 28),
                          bg_bgr=(255, 255, 255), border_bgr=(105, 185, 16), radius=14)
        draw_vn_text_centered(canvas, "ĐÃ LƯU GÓC CHỤP!",
                              (CAM_WIDTH // 2 - 140, HEADER_HEIGHT + CAM_HEIGHT // 2 - 28,
                               CAM_WIDTH // 2 + 140, HEADER_HEIGHT + CAM_HEIGHT // 2 + 28),
                              size=18, color_bgr=(105, 185, 16), bold=True)

    # 2. TOP CARD: Nen trang sang trong (#ffffff), vien nhe sky (#e0f2fe)
    draw_rounded_card(canvas, (12, 10), (TOTAL_WIDTH - 12, HEADER_HEIGHT - 8),
                      bg_bgr=(255, 255, 255), border_bgr=(254, 242, 224), radius=12)
    
    # Chu mau xanh dam Navy (#0f172a) va xam hien dai (#334155)
    draw_vn_text(canvas, step.title, (26, 16), size=16, color_bgr=(199, 132, 2), bold=True)  # Xanh Blue Modern GUI
    draw_vn_text(canvas, step.guide, (26, 42), size=14, color_bgr=(71, 85, 105))

    # Badge % hình Pill bo tròn: Nen nhe, vien status_bgr, so can giua tuyet doi
    pill_rect = (TOTAL_WIDTH - 105, 14, TOTAL_WIDTH - 22, HEADER_HEIGHT - 14)
    # Mau nen pill: Xanh la nhe (235, 253, 240) neu 100%, Xanh duong nhe neu >=60%
    pill_bg = (235, 253, 240) if in_range else ((254, 245, 235) if percent >= 60 else (240, 240, 254))
    draw_rounded_card(canvas, (pill_rect[0], pill_rect[1]), (pill_rect[2], pill_rect[3]),
                      bg_bgr=pill_bg, border_bgr=status_bgr, radius=18)
    draw_vn_text_centered(canvas, f"{percent}%", pill_rect, size=18, color_bgr=status_bgr, bold=True)

    # 3. BOTTOM CARD: Nen trang sang trong
    fy = HEADER_HEIGHT + CAM_HEIGHT
    draw_rounded_card(canvas, (12, fy + 8), (TOTAL_WIDTH - 12, TOTAL_HEIGHT - 10),
                      bg_bgr=(255, 255, 255), border_bgr=(254, 242, 224), radius=12)

    # Thanh tiến độ nhuyễn mịn (Smooth Progress Bar)
    bar_x1, bar_x2 = 24, TOTAL_WIDTH - 24
    bar_y1, bar_y2 = fy + 16, fy + 24
    cv2.rectangle(canvas, (bar_x1, bar_y1), (bar_x2, bar_y2), (240, 247, 255), -1)  # Nen ranh nhe
    if hold_frac > 0:
        fill_w = int((bar_x2 - bar_x1) * hold_frac)
        cv2.rectangle(canvas, (bar_x1, bar_y1), (bar_x1 + fill_w, bar_y2), (105, 185, 16), -1)

    # Dòng chữ trạng thái duoc can giua hoan toan
    text_rect = (24, fy + 28, TOTAL_WIDTH - 24, TOTAL_HEIGHT - 12)
    if warning:
        if "nguoi" in warning.lower() or "người" in warning.lower():
            draw_vn_text_centered(canvas, warning.upper(), text_rect, size=14, color_bgr=(60, 60, 230), bold=True)
        else:
            draw_vn_text_centered(canvas, warning, text_rect, size=14, color_bgr=(60, 60, 230), bold=True)
    elif in_range:
        draw_vn_text_centered(canvas, "Tư thế chuẩn xác • Vui lòng giữ yên để lưu mẫu...", text_rect, size=14, color_bgr=(105, 185, 16), bold=True)
    else:
        draw_vn_text_centered(canvas, "Điều chỉnh hướng mặt theo chỉ dẫn để đạt 100%", text_rect, size=14, color_bgr=(100, 116, 139))

# Chup anh + validate
def capture_encodings(cfg: Config, username: str = "") -> Optional[list[np.ndarray]]:
    from core.fiqa import FaceQualityAssessor

    detector = YuNetDetector()
    embedder = ArcFaceEmbedder()
    assessor = FaceQualityAssessor(max_roll=cfg.max_roll_deg)

    cap = open_camera(cfg)

    # -------------------------------------------------------------
    # GIAI DOAN 0: MAN HINH CHO (WELCOME & READY SCREEN)
    # Nguoi dung can chu dong nhan phim [SPACE] de bat dau tien trinh chup!
    # -------------------------------------------------------------
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    # Can giua cua so man hinh may tinh tren Windows
    try:
        import ctypes
        u32 = ctypes.windll.user32
        sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
        win_x = max(0, (sw - TOTAL_WIDTH) // 2)
        win_y = max(0, (sh - TOTAL_HEIGHT) // 2 - 30)
        cv2.moveWindow(WINDOW, win_x, win_y)
    except Exception:
        pass
    ready_to_start = False
    read_fail_welcome = 0

    try:
        while not ready_to_start:
            ok, frame = cap.read()
            if not ok or frame is None:
                read_fail_welcome += 1
                if read_fail_welcome > cfg.max_read_failures:
                    log.error("Camera khong tra ve khung hinh tai man hinh cho.")
                    return None
                time.sleep(0.02)
                continue
            read_fail_welcome = 0

            h, w = frame.shape[:2]
            dets = detector.detect(frame)
            prev_box_mirrored = None
            prev_warning = ""

            if len(dets) > 0:
                dets.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                pb = dets[0][0]
                prev_box_mirrored = (w - pb[1], pb[0], w - pb[3], pb[2])
                if len(dets) > 1:
                    p_area = (pb[2] - pb[0]) * (pb[1] - pb[3])
                    for od in dets[1:]:
                        ob = od[0]
                        if (ob[2] - ob[0]) * (ob[1] - ob[3]) > 0.55 * p_area:
                            prev_warning = "Phat hien 2 nguoi: Vui long de nguoi ben canh tranh khoi camera"
                            break

            cam_mirrored = cv2.flip(frame, 1)
            canvas = make_enroll_canvas(cam_mirrored)
            draw_welcome_hud(canvas, username, prev_box_mirrored, prev_warning)
            cv2.imshow(WINDOW, canvas)

            k = cv2.waitKey(15) & 0xFF
            if k == ord(" "):  # Phím Space
                ready_to_start = True
            elif k in (ord("q"), 27):  # Phím Q hoặc ESC
                log.info("Nguoi dung da huy bo qua trinh dang ky.")
                return None

        # -------------------------------------------------------------
        # GIAI DOAN 1: TIEN TRINH CHUP 5 GOC CHUAN
        # -------------------------------------------------------------
        encodings: list[np.ndarray] = []
        step_idx = 0
        step_start = time.time()
        hold_start = None
        cooldown_until = 0.0
        flash_until = 0.0
        smooth: tuple[float, float] | None = None
        baseline = (0.0, 0.0)
        best = None
        read_fail = 0
        last_debug = 0.0

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
            raw_detections = detector.detect(frame)
            
            # 1. Loc khuon mat rac o qua xa (chieu rong hoac cao < 70px)
            detections = [d for d in raw_detections if (d[0][2] - d[0][0]) >= 70 and (d[0][1] - d[0][3]) >= 70]
            n_faces = len(detections)

            percent, in_range = 0, False
            warning = ""
            hold_frac = 0.0
            box_mirrored = None
            extra_boxes_mirrored = []
            can_capture = False
            active_box = None
            active_landmarks = None
            rel_yaw = rel_pitch = 0.0
            raw_yaw = raw_pitch = 0.0

            if n_faces == 0:
                warning = "Khong tim thay mat"
                smooth = None
            else:
                # Sap xep theo dien tich Bounding Box giam dan de tim chu the chinh
                detections.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                primary_det = detections[0]
                box, landmarks, _ = primary_det
                top, right, bottom, left = box
                box_mirrored = (w - right, top, w - left, bottom)
                primary_area = (bottom - top) * (right - left)

                # Kiem tra cac khuon mat phu con lai
                crowded = False
                for other_det in detections[1:]:
                    ob = other_det[0]
                    other_area = (ob[2] - ob[0]) * (ob[1] - ob[3])
                    # Neu co khuon mat phu lon (dien tich > 55% chu the chinh) -> Nguy co co nguoi dung ngay ben canh
                    if other_area > 0.55 * primary_area:
                        crowded = True
                        extra_boxes_mirrored.append((w - ob[1], ob[0], w - ob[3], ob[2]))

                if crowded:
                    warning = "Phát hiện nhiều người: Vui lòng để người phía sau tránh khỏi camera"
                    smooth = None
                else:
                    pose = assessor.estimate_pose(landmarks, w, h, cfg.yaw_sign, cfg.pitch_sign)
                    if pose is None:
                        warning = "Đang phân tích hướng khuôn mặt..."
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
                            log.info("[%s] raw yaw=%+.1f pitch=%+.1f roll=%+.1f | rel yaw=%+.1f pitch=%+.1f",
                                     step.name, raw_yaw, raw_pitch, roll, rel_yaw, rel_pitch)

                        if step_idx == 0:
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
                            warning = "Vui lòng giữ thẳng đầu, không nghiêng sang hai bên"
                        elif now < cooldown_until:
                            warning = "Đã lưu góc thành công, chuẩn bị góc tiếp theo..."
                        else:
                            can_capture = True
                            active_box = box
                            active_landmarks = landmarks

            # Kiem tra do on dinh cua dau, dung sai 5.0 giup khong bi reset do run nhe
            is_head_still = False
            if smooth is not None:
                d_angle = abs(raw_yaw - smooth[0]) + abs(raw_pitch - smooth[1])
                is_head_still = d_angle < 5.0

            if can_capture and in_range and is_head_still and active_box is not None and smooth is not None:
                if hold_start is None:
                    hold_start = now
                    best = None
                hold_frac = min(1.0, (now - hold_start) / cfg.hold_sec)
                sharp = sharpness(frame, active_box)
                if best is None or sharp > best[0]:
                    best = (sharp, frame.copy(), active_box, active_landmarks, smooth)
                auto_ready = hold_frac >= 1.0
            else:
                hold_start = None
                best = None
                auto_ready = False

            cam_mirrored = cv2.flip(frame, 1)
            canvas = make_enroll_canvas(cam_mirrored)
            draw_hud(canvas, step, percent, in_range, hold_frac,
                     warning, now < flash_until, box_mirrored, extra_boxes_mirrored)
            cv2.imshow(WINDOW, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return None

            if auto_ready and best is not None:
                _, best_frame, best_box, best_lms, best_smooth = best
                bh, bw = best_frame.shape[:2]

                # Can chinh khuon mat bang Similarity Transform 5 moc landmarks
                emb = embedder.extract(best_frame, clamp_box(best_box, bw, bh), landmarks=best_lms)
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
                for _ in range(4):
                    cap.grab()

        return encodings
    finally:
        cap.release()
        cv2.destroyAllWindows()

# Nhận tên người dùng từ dòng lệnh (ví dụ: python scripts/enroll.py ADMIN).
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Auto Enrollment")
    parser.add_argument("username", nargs="?", help="Username")
    parser.add_argument("--user", dest="user_opt", default="", help="Username (tuy chon)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--data-dir", default=DEFAULT_EMBEDDINGS_DIR,
                        help="Thu muc luu embedding")
    parser.add_argument("--append", action="store_true",
                        help="Them vao embedding da co")
    parser.add_argument("--max-vectors", type=int, default=5,
                        help="So luong Pose Prototypes toi da (mac dinh: 5)")
    parser.add_argument("--debug", action="store_true", help="In thong so chan doan")
    args = parser.parse_args()

    username = (args.username or args.user_opt or input("Username: ")).strip()
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

    encodings = capture_encodings(cfg, username=username)
    if encodings is None or len(encodings) != len(STEPS):
        log.error("Chua hoan tat dang ky.")
        return 1

    # RANG BUOC CHONG TRUNG LAP: Kiem tra voi cac khuon mat da co trong he thong
    all_existing = load_all_enrolled_templates(args.data_dir)
    probe_emb = encodings[0]  # Goc nhin thang chinh dien (Baseline)
    embedder = ArcFaceEmbedder()

    matched_existing_user = None
    highest_sim = 0.0

    for ex_user, ex_templates in all_existing:
        if ex_user.lower() == username.lower():
            continue  # Cung mot ten thi cho phep cap nhat (ghi de)
        
        sims = [float(1.0 - embedder.compute_distance(probe_emb, t)) for t in ex_templates]
        max_sim = max(sims) if sims else 0.0
        if max_sim > highest_sim:
            highest_sim = max_sim
            if max_sim >= 0.65:
                matched_existing_user = ex_user

    if matched_existing_user is not None:
        log.warning(
            "[CANH BAO] Phat hien khuon mat nay DA DUOC DANG KY truoc do duoi ten: '%s' (Do tuong dong: %.1f%%)!",
            matched_existing_user, highest_sim * 100
        )
        print("\n" + "="*60)
        print(f"[!] KHUON MAT NAY DA CO TRONG MAY DUOI TEN: '{matched_existing_user}' (Khop {highest_sim*100:.1f}%)")
        print(f"    He thong tu choi luu them ban ghi moi '{username}' de tranh trung lap.")
        print("="*60 + "\n")
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Khuôn mặt này ĐÃ ĐƯỢC ĐĂNG KÝ trước đó với tên: '{matched_existing_user}'!\n"
                    f"(Độ khớp nhận diện: {highest_sim*100:.1f}%)\n\n"
                    "Hệ thống sẽ không lưu thêm bản ghi mới để tránh trùng lặp dữ liệu.\n"
                    "Nếu muốn đổi tên, vui lòng xóa bản ghi cũ trước.",
                    "Phát hiện khuôn mặt trùng lặp",
                    0x00000030 | 0x00010000  # MB_ICONWARNING | MB_SETFOREGROUND
                )
            else:
                import subprocess
                subprocess.Popen([
                    "notify-send", "Phát hiện khuôn mặt trùng lặp",
                    f"Khuôn mặt này đã đăng ký dưới tên '{matched_existing_user}' ({highest_sim*100:.1f}%)."
                ])
        except Exception:
            pass
        return 2

    path, n = save_user_embeddings_atomic(username, encodings, args.data_dir,
                                          append=args.append, max_vectors=args.max_vectors)
    log.info("[OK] THANH CONG: file %s hien co %d vector ArcFace", path, n)
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Đã đăng ký thành công 5 góc mặt cho: '{username}'!\n"
                "Dữ liệu đã sẵn sàng để mở khóa màn hình Windows.",
                "Đăng ký FaceID thành công",
                0x00000040 | 0x00010000  # MB_ICONINFORMATION | MB_SETFOREGROUND
            )
        else:
            import subprocess
            subprocess.Popen([
                "notify-send", "Đăng ký FaceID thành công",
                f"Đã đăng ký thành công 5 góc mặt cho '{username}'!"
            ])
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())