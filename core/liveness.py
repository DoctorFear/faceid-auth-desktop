import os
import sys
import warnings
from contextlib import contextmanager

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["GLOG_minloglevel"] = "3"
os.environ["ABSL_LOG_LEVEL"] = "3"
warnings.filterwarnings("ignore")

import time
import random
from collections import deque
from enum import Enum
import cv2
import numpy as np


@contextmanager
def suppress_c_stderr():
    """Chuyen huong stderr o muc C-level de tat triet de log Abseil/TFLite."""
    try:
        stderr_fd = sys.stderr.fileno()
        saved_stderr_fd = os.dup(stderr_fd)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, stderr_fd)
        os.close(devnull_fd)
        try:
            yield
        finally:
            os.dup2(saved_stderr_fd, stderr_fd)
            os.close(saved_stderr_fd)
    except Exception:
        yield


class ActionType(Enum):
    TURN_LEFT       = "QUAY SANG TRAI <--"
    TURN_RIGHT      = "QUAY SANG PHAI -->"
    NOD_DOWN        = "CUI DAU XUONG [v]"
    LOOK_UP         = "NGUOC MAT LEN [^]"
    BLINK_ONCE      = "CHOP MAT 1 CAI [o_o]"
    BLINK_TWICE     = "CHOP MAT 2 CAI [o_o]x2"
    SMILE           = "CUOI TUOI :D"
    TWO_FINGERS     = "GIO 2 NGON TAY (CHU V) [V]"


class ChallengeResponseDetector:
    TW, TH = 48, 24
    CALIB_FRAMES = 6
    BLINK_MAX_CLOSED = 1.3

    SMILE_BASE_FRAMES = 6
    SMILE_WIDTH_GAIN = 0.04
    SMILE_WIDTH_GAIN_SOFT = 0.025
    SMILE_LIFT_GAIN = 0.015
    SMILE_TEETH_SOFT = 0.06
    SMILE_TEETH_STRONG = 0.15
    SMILE_MIN_FRAMES = 2
    SMILE_MIN_SECONDS = 0.15

    FINGER_HOLD_FRAMES = 4

    def __init__(self, timeout_sec: float = 16.0):
        self.timeout_sec = timeout_sec
        self.current_action: ActionType | None = None
        self.challenge_start_time = 0.0
        self._mp_hands = None
        self._reset_state()

    def _get_mp_hands(self):
        if self._mp_hands is None:
            try:
                with suppress_c_stderr():
                    import mediapipe as mp
                    self._mp_hands = mp.solutions.hands.Hands(
                        static_image_mode=False,
                        max_num_hands=1,
                        model_complexity=0,
                        min_detection_confidence=0.55,
                        min_tracking_confidence=0.55
                    )
            except Exception as e:
                self._mp_hands = None
        return self._mp_hands

    def _reset_state(self):
        self._pose_centered = False
        self._pose_hold_frames = 0

        self.blink_count = 0
        self.eye_closed = False
        self.last_blink_time = 0.0
        self._closed_start = 0.0
        self.eye_tmpl = None
        self.eye_depth = None
        self.closed_th = 0.0
        self.open_th = 0.0
        self._calib = []

        self.baseline_mouth_ratio = None
        self.baseline_teeth = 0.0
        self._base_ratios = []
        self._base_teeth = []
        self._ratio_hist = deque(maxlen=3)
        self._lift_hist = deque(maxlen=3)
        self.baseline_lift = 0.0
        self._base_lifts = []
        self.smile_frames = 0
        self._smile_start = 0.0

        self.hand_match_frames = 0
        self._pose_base = None
        self._pose_buf = []

    def reset_challenge(self, action: ActionType | None = None) -> ActionType:
        self.current_action = action or random.choice(list(ActionType))
        self.challenge_start_time = time.time()
        self._reset_state()
        return self.current_action

    # --- 1. POSE ---
    def _check_pose(self, pose: tuple[float, float, float] | None, act: ActionType) -> tuple[bool, str]:
        if pose is None:
            return False, "Dang tim khuon mat..."
        yaw, pitch, _ = pose

        # Lay mau tu the nhin thang lam goc (0, 0)
        if self._pose_base is None:
            self._pose_buf.append((yaw, pitch))
            arr = np.array(self._pose_buf)
            if len(arr) > 1 and (np.ptp(arr[:, 0]) > 8 or np.ptp(arr[:, 1]) > 8):
                self._pose_buf = [(yaw, pitch)]  # dang cu dong -> lay lai
                return False, "Giu dau yen, nhin thang..."
            if len(arr) < 8:
                return False, f"Nhin thang de lay mau... ({len(arr)}/8)"
            self._pose_base = (float(np.median(arr[:, 0])), float(np.median(arr[:, 1])))
            return False, "San sang! Hay lam thu thach"

        dyaw = yaw - self._pose_base[0]
        dpitch = pitch - self._pose_base[1]

        if act == ActionType.TURN_LEFT:
            target_met = dyaw < -9.0
            hint = f"Quay sang TRAI (dYaw: {dyaw:+.1f} / < -9)"
        elif act == ActionType.TURN_RIGHT:
            target_met = dyaw > 9.0
            hint = f"Quay sang PHAI (dYaw: {dyaw:+.1f} / > +9)"
        elif act == ActionType.NOD_DOWN:
            target_met = dpitch < -7.0
            hint = f"Cui nhe cam (dPitch: {dpitch:+.1f} / < -7)"
        else:  # LOOK_UP
            target_met = dpitch > 8.0
            hint = f"Nang nhe cam (dPitch: {dpitch:+.1f} / > +8)"

        if target_met:
            self._pose_hold_frames += 1
            if self._pose_hold_frames >= 3:
                return True, "Thanh cong: Da lam dung huong!"
            return False, f"Giu nguyen ({self._pose_hold_frames}/3)..."

        self._pose_hold_frames = max(0, self._pose_hold_frames - 1)
        return False, hint        
        if pose is None:
            return False, "Dang tim khuon mat..."
        yaw, pitch, _ = pose

        if not self._pose_centered:
            if abs(yaw) < 6.0 and abs(pitch) < 6.0:
                self._pose_centered = True
            return False, f"Hay nhin THANG truoc (Yaw:{yaw:+.0f}, Pitch:{pitch:+.0f})"

        target_met = False
        hint = ""
        if act == ActionType.TURN_LEFT:
            target_met = (yaw < -9.0)
            hint = f"Quay sang TRAI (Yaw: {yaw:+.1f} / < -9)"
        elif act == ActionType.TURN_RIGHT:
            target_met = (yaw > 9.0)
            hint = f"Quay sang PHAI (Yaw: {yaw:+.1f} / > +9)"
        elif act == ActionType.NOD_DOWN:
            target_met = (pitch < -7.0)
            hint = f"Cui nhe cam (Pitch: {pitch:+.1f} / < -7)"
        elif act == ActionType.LOOK_UP:
            target_met = (pitch > 8.0)
            hint = f"Nang nhe cam (Pitch: {pitch:+.1f} / > +8)"

        if target_met:
            self._pose_hold_frames += 1
            if self._pose_hold_frames >= 3:
                return True, "Thanh cong: Da lam dung huong!"
            return False, f"Giu nguyen ({self._pose_hold_frames}/3)..."
        else:
            self._pose_hold_frames = max(0, self._pose_hold_frames - 1)
            return False, hint

    # --- 2. CHOP MAT ---
    def _eye_patches(self, gray: np.ndarray, landmarks: np.ndarray):
        re, le = landmarks[0][:2], landmarks[1][:2]
        d = float(np.linalg.norm(re - le))
        if d < 18:
            return None
        rx, ry = 0.20 * d, 0.10 * d
        px, py = 0.08 * d, 0.06 * d
        tw, th = self.TW, self.TH
        sw = int(round(tw * (rx + px) / rx))
        sh = int(round(th * (ry + py) / ry))

        def sub(cx, cy, hw, hh):
            return cv2.getRectSubPix(gray, (max(3, int(round(2 * hw))), max(3, int(round(2 * hh)))), (cx, cy))

        out = []
        for c in (re, le):
            cx, cy = float(c[0]), float(c[1])
            tpl = cv2.resize(sub(cx, cy, rx, ry), (tw, th), interpolation=cv2.INTER_CUBIC).astype(np.float32)
            srch = cv2.resize(sub(cx, cy, rx + px, ry + py), (sw, sh), interpolation=cv2.INTER_CUBIC).astype(np.float32)
            ref = float(np.median(sub(cx, cy, 0.28 * d, 0.16 * d)))
            ctr = float(np.percentile(sub(cx, cy, 0.09 * d, 0.045 * d), 25))
            out.append({"tpl": tpl, "srch": srch, "depth": ref - ctr})
        return out

    @staticmethod
    def _ncc(a: np.ndarray, b: np.ndarray) -> float:
        res = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(res.max(), nan=0.0, posinf=0.0, neginf=0.0))

    def _eye_score(self, eyes) -> float:
        s = []
        for i, e in enumerate(eyes):
            ncc = self._ncc(e["srch"], self.eye_tmpl[i])
            dr = float(np.clip(e["depth"] / self.eye_depth[i], 0.0, 1.0))
            s.append(0.6 * ncc + 0.4 * dr)
        return float(np.mean(s))

    def _check_blink_template(self, frame_bgr: np.ndarray, landmarks: np.ndarray, target_blinks: int):
        gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (3, 3), 0)
        eyes = self._eye_patches(gray, landmarks)
        if eyes is None:
            return False, "Mat qua xa camera", 0.0

        if self.eye_tmpl is None:
            self._calib.append(eyes)
            if len(self._calib) < self.CALIB_FRAMES:
                return False, f"Nhin tu nhien de lay mau... ({len(self._calib)}/{self.CALIB_FRAMES})", 1.0

            tmpls, depths, ok = [], [], True
            for i in range(2):
                med = np.median(np.stack([f[i]["tpl"] for f in self._calib]), axis=0).astype(np.float32)
                if min(self._ncc(f[i]["srch"], med) for f in self._calib) < 0.70:
                    ok = False
                tmpls.append(med)
                depths.append(max(float(np.median([f[i]["depth"] for f in self._calib])), 5.0))
            if not ok:
                self._calib = []
                return False, "Lay mau lai mat...", 1.0
            self.eye_tmpl, self.eye_depth = tmpls, depths

            sc = np.array([self._eye_score(f) for f in self._calib])
            m, sigma = float(sc.mean()), float(sc.std())
            self.closed_th = m - max(0.16, 3.5 * sigma)
            self.open_th = m - max(0.08, 1.8 * sigma)
            self._calib = []
            return False, "San sang! Hay chop mat", 1.0

        score = self._eye_score(eyes)
        now = time.time()
        if score < self.closed_th:
            if not self.eye_closed:
                self.eye_closed = True
                self._closed_start = now
        elif score >= self.open_th:
            if self.eye_closed:
                dur = now - self._closed_start
                if dur <= self.BLINK_MAX_CLOSED and (now - self.last_blink_time) > 0.12:
                    self.blink_count += 1
                    self.last_blink_time = now
                self.eye_closed = False
            else:
                for i, e in enumerate(eyes):
                    self.eye_tmpl[i] = 0.94 * self.eye_tmpl[i] + 0.06 * e["tpl"]

        if self.blink_count >= target_blinks:
            return True, f"Thanh cong: Da chop {target_blinks} lan!", score

        state = "NHAM" if self.eye_closed else "MO"
        return False, f"Da chop: {self.blink_count}/{target_blinks} ({state} {int(score*100)}%)", score

    # --- 3. CUOI ---
    @staticmethod
    def _teeth_ratio(frame_bgr: np.ndarray, rm: np.ndarray, lm: np.ndarray) -> float:
        mw = float(np.linalg.norm(rm - lm))
        cx, cy = (rm[0] + lm[0]) / 2.0, (rm[1] + lm[1]) / 2.0
        pw, ph = max(8, int(mw * 0.7)), max(6, int(mw * 0.35))
        patch = cv2.getRectSubPix(frame_bgr, (pw, ph), (float(cx), float(cy)))
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        teeth = (hsv[..., 1] < 75) & (hsv[..., 2] > 135)
        return float(teeth.mean())

    def _check_smile_adaptive(self, landmarks: np.ndarray, frame_bgr: np.ndarray | None = None):
        re, le = landmarks[0][:2], landmarks[1][:2]
        nose = landmarks[2][:2]
        rm, lm = landmarks[3][:2], landmarks[4][:2]
        eye_dist = float(np.linalg.norm(re - le))
        mouth_dist = float(np.linalg.norm(rm - lm))
        if eye_dist < 1e-3:
            return False, "Dang tim mat...", 0.0

        ratio = mouth_dist / eye_dist
        lift_d = ((rm[1] + lm[1]) / 2.0 - nose[1]) / eye_dist
        teeth = self._teeth_ratio(frame_bgr, rm, lm) if frame_bgr is not None else 0.0

        if self.baseline_mouth_ratio is None:
            self._base_ratios.append(ratio)
            self._base_lifts.append(lift_d)
            self._base_teeth.append(teeth)
            remain = self.SMILE_BASE_FRAMES - len(self._base_ratios)
            if remain > 0:
                return False, f"Giu mat tu nhien, chua cuoi... ({remain})", ratio
            self.baseline_mouth_ratio = float(np.median(self._base_ratios))
            self.baseline_lift = float(np.median(self._base_lifts))
            self.baseline_teeth = float(np.median(self._base_teeth))
            return False, "Bay gio hay cuoi mim hoac cuoi thay rang :D", ratio

        self._ratio_hist.append(ratio)
        self._lift_hist.append(lift_d)
        r = float(np.median(self._ratio_hist))
        width_gain = r / self.baseline_mouth_ratio - 1.0
        lift_gain = self.baseline_lift - float(np.median(self._lift_hist))
        teeth_gain = teeth - self.baseline_teeth

        smiling = (
            width_gain >= self.SMILE_WIDTH_GAIN
            or (width_gain >= self.SMILE_WIDTH_GAIN_SOFT and lift_gain >= self.SMILE_LIFT_GAIN)
            or (width_gain >= 0.02 and teeth_gain >= self.SMILE_TEETH_SOFT)
            or teeth_gain >= self.SMILE_TEETH_STRONG
        )

        now = time.time()
        if smiling:
            if self.smile_frames == 0:
                self._smile_start = now
            self.smile_frames += 1
            if self.smile_frames >= self.SMILE_MIN_FRAMES and (now - self._smile_start) >= self.SMILE_MIN_SECONDS:
                return True, "Thanh cong: Da cuoi!", r
            return False, f"Dang cuoi... ({self.smile_frames}/{self.SMILE_MIN_FRAMES})", r

        self.smile_frames = 0
        if width_gain < 0.02:
            self.baseline_mouth_ratio = 0.98 * self.baseline_mouth_ratio + 0.02 * r
        return False, f"Cuoi mim hoac cuoi thay rang :D (+{width_gain*100:+.0f}%)", r

    # --- 4. GIO 2 NGON TAY (CHỮ V) ---
    def _check_two_fingers(self, frame_bgr: np.ndarray):
        hands = self._get_mp_hands()
        if hands is None:
            return False, "Chua khoi tao duoc MediaPipe Hands", []

        h, w = frame_bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))

        try:
            with suppress_c_stderr():
                res = hands.process(rgb)
        except Exception:
            return False, "Dang xu ly ban tay...", []

        if not res or not res.multi_hand_landmarks:
            self.hand_match_frames = max(0, self.hand_match_frames - 1)
            return False, "Hay dua ban tay vao khung hinh camera", []

        def is_finger_up(pts, tip_idx, pip_idx):
            return np.linalg.norm(pts[tip_idx] - pts[0]) > 1.15 * np.linalg.norm(pts[pip_idx] - pts[0])

        def is_finger_down(pts, tip_idx, pip_idx):
            return np.linalg.norm(pts[tip_idx] - pts[0]) < 1.12 * np.linalg.norm(pts[pip_idx] - pts[0])

        is_valid_v = False
        v_tips = []

        for hl in res.multi_hand_landmarks:
            pts = np.array([[lm.x * w, lm.y * h] for lm in hl.landmark], dtype=np.float32)

            idx_up = is_finger_up(pts, 8, 6)
            mid_up = is_finger_up(pts, 12, 10)
            ring_down = is_finger_down(pts, 16, 14)
            pinky_down = is_finger_down(pts, 20, 18)

            palm_w = float(np.linalg.norm(pts[9] - pts[0])) + 1e-6
            apart = float(np.linalg.norm(pts[8] - pts[12])) > 0.28 * palm_w

            if idx_up and mid_up and ring_down and pinky_down and apart:
                is_valid_v = True
                v_tips = [(int(pts[8][0]), int(pts[8][1])), (int(pts[12][0]), int(pts[12][1]))]
                break

        if is_valid_v:
            self.hand_match_frames += 1
            if self.hand_match_frames >= self.FINGER_HOLD_FRAMES:
                return True, "Thanh cong: Da xac nhan chu V!", v_tips
            return False, f"Giu yen chu V ({self.hand_match_frames}/{self.FINGER_HOLD_FRAMES})...", v_tips
        else:
            self.hand_match_frames = max(0, self.hand_match_frames - 1)
            return False, "Chi gio dung 2 ngon (tro + giua), gap cac ngon con lai", []

    def check_action(
        self,
        frame_bgr: np.ndarray,
        pose: tuple[float, float, float] | None,
        landmarks: np.ndarray | None,
        face_box: tuple | None
    ) -> tuple[bool, str, dict]:
        debug_info = {"fingertips": []}

        if self.current_action is None:
            return False, "Chua khoi tao thu thach", debug_info

        now = time.time()
        if now - self.challenge_start_time > self.timeout_sec:
            return False, "HET GIO! Nhan 'r' de thu thach moi", debug_info

        act = self.current_action

        if act in [ActionType.TURN_LEFT, ActionType.TURN_RIGHT, ActionType.NOD_DOWN, ActionType.LOOK_UP]:
            passed, hint = self._check_pose(pose, act)
            return passed, hint, debug_info

        if act in (ActionType.BLINK_ONCE, ActionType.BLINK_TWICE):
            if landmarks is None:
                return False, "Dang tim mat...", debug_info
            target = 1 if act == ActionType.BLINK_ONCE else 2
            passed, hint, _ = self._check_blink_template(frame_bgr, landmarks, target_blinks=target)
            return passed, hint, debug_info

        if act == ActionType.SMILE:
            if landmarks is None:
                return False, "Dang tim mat...", debug_info
            passed, hint, _ = self._check_smile_adaptive(landmarks, frame_bgr)
            return passed, hint, debug_info

        if act == ActionType.TWO_FINGERS:
            passed, hint, tips = self._check_two_fingers(frame_bgr)
            debug_info["fingertips"] = tips
            return passed, hint, debug_info

        return False, "Dang cho...", debug_info
