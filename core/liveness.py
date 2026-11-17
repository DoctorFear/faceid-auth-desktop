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
from core.constants import (
    DEFAULT_CHALLENGE_TIMEOUT_SEC,
    LIVENESS_BASE_MAX_YAW,
    LIVENESS_BASE_MAX_PITCH,
    LIVENESS_BASE_CALIB_FRAMES,
    LIVENESS_TURN_LEFT_DYAW,
    LIVENESS_TURN_LEFT_ABS_YAW,
    LIVENESS_TURN_RIGHT_DYAW,
    LIVENESS_TURN_RIGHT_ABS_YAW,
    LIVENESS_NOD_DOWN_DPITCH,
    LIVENESS_NOD_DOWN_ABS_PITCH,
    LIVENESS_LOOK_UP_DPITCH,
    LIVENESS_LOOK_UP_ABS_PITCH,
    LIVENESS_POSE_HOLD_FRAMES,
)


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
    TURN_LEFT       = "Vui lòng quay mặt sang trái"
    TURN_RIGHT      = "Vui lòng quay mặt sang phải"
    NOD_DOWN        = "Vui lòng cúi nhẹ đầu"
    LOOK_UP         = "Vui lòng ngẩng nhẹ đầu"
    BLINK_ONCE      = "Vui lòng chớp mắt 1 lần"
    BLINK_TWICE     = "Vui lòng chớp mắt 2 lần"
    SMILE           = "Vui lòng mỉm cười nhẹ"
    TWO_FINGERS     = "Vui lòng giơ ngón tay chữ V"


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

    def __init__(self, timeout_sec: float = DEFAULT_CHALLENGE_TIMEOUT_SEC, preload_hands: bool = True):
        self.timeout_sec = timeout_sec
        self.current_action: ActionType | None = None
        self.challenge_start_time = 0.0
        self._mp_hands = None
        self._reset_state()

        if preload_hands:
            self._warmup_mp_hands()

    def _warmup_mp_hands(self):
        """Warm-up truoc MediaPipe Hands voi frame rong de tranh stutter khi dang chay."""
        hands = self._get_mp_hands()
        if hands is not None:
            try:
                dummy = np.zeros((240, 320, 3), dtype=np.uint8)
                with suppress_c_stderr():
                    hands.process(dummy)
            except Exception:
                pass

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
            except Exception:
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
        self._pose_ema = None

    def reset_challenge(self, action: ActionType | None = None) -> ActionType:
        self.current_action = action or random.choice(list(ActionType))
        self.challenge_start_time = time.time()
        self._reset_state()
        return self.current_action

    # --- 1. POSE ---
    def _check_pose(self, pose: tuple[float, float, float] | None, act: ActionType) -> tuple[bool, str]:
        if pose is None:
            return False, "Vui lòng đưa khuôn mặt vào khung hình..."
        raw_yaw, raw_pitch, _ = pose

        # Lam muot goc bang EMA de triet tieu nhieu (jitter) giua cac frame
        if self._pose_ema is None:
            self._pose_ema = (raw_yaw, raw_pitch)
        else:
            self._pose_ema = (
                0.60 * raw_yaw + 0.40 * self._pose_ema[0],
                0.60 * raw_pitch + 0.40 * self._pose_ema[1],
            )
        yaw, pitch = self._pose_ema

        # Chi khoa moc xuat phat khi khuon mat dang o tu the thang va on dinh qua LIVENESS_BASE_CALIB_FRAMES frame
        if self._pose_base is None:
            if abs(yaw) <= LIVENESS_BASE_MAX_YAW and abs(pitch) <= LIVENESS_BASE_MAX_PITCH:
                self._pose_buf.append((yaw, pitch))
                if len(self._pose_buf) >= LIVENESS_BASE_CALIB_FRAMES:
                    med_y = float(np.median([p[0] for p in self._pose_buf]))
                    med_p = float(np.median([p[1] for p in self._pose_buf]))
                    # Kiem tra dau khong dang chuyen dong manh trong luc chot moc
                    spread_y = max(p[0] for p in self._pose_buf) - min(p[0] for p in self._pose_buf)
                    spread_p = max(p[1] for p in self._pose_buf) - min(p[1] for p in self._pose_buf)
                    if spread_y <= 5.0 and spread_p <= 4.0:
                        self._pose_base = (med_y, med_p)
                        self._pose_buf.clear()
                        return False, act.value
                    else:
                        # Giữ lại nửa cuối buffer nếu đầu vừa mới ổn định lại
                        self._pose_buf = self._pose_buf[-(LIVENESS_BASE_CALIB_FRAMES // 2):]
                return False, act.value
            else:
                self._pose_buf.clear()
                return False, "Vui lòng nhìn thẳng vào camera để bắt đầu..."

        dyaw = yaw - self._pose_base[0]
        dpitch = pitch - self._pose_base[1]

        if act == ActionType.TURN_LEFT:
            target_met = dyaw < LIVENESS_TURN_LEFT_DYAW
            hint = "Vui lòng quay mặt sang trái"
        elif act == ActionType.TURN_RIGHT:
            target_met = dyaw > LIVENESS_TURN_RIGHT_DYAW
            hint = "Vui lòng quay mặt sang phải"
        elif act == ActionType.NOD_DOWN:
            target_met = dpitch < LIVENESS_NOD_DOWN_DPITCH
            hint = "Vui lòng cúi nhẹ đầu"
        else:  # LOOK_UP
            target_met = dpitch > LIVENESS_LOOK_UP_DPITCH
            hint = "Vui lòng ngẩng nhẹ đầu"

        if target_met:
            self._pose_hold_frames += 1
            if self._pose_hold_frames >= LIVENESS_POSE_HOLD_FRAMES:
                return True, "Đã xác thực cử động thành công!"
            return False, f"Giữ nguyên tư thế ({self._pose_hold_frames}/{LIVENESS_POSE_HOLD_FRAMES})..."

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
            return False, "Vui lòng giữ khoảng cách vừa phải...", 0.0

        if self.eye_tmpl is None:
            self._calib.append(eyes)
            if len(self._calib) < self.CALIB_FRAMES:
                return False, "Đang định vị hướng mắt...", 1.0

            tmpls, depths, ok = [], [], True
            for i in range(2):
                med = np.median(np.stack([f[i]["tpl"] for f in self._calib]), axis=0).astype(np.float32)
                if min(self._ncc(f[i]["srch"], med) for f in self._calib) < 0.70:
                    ok = False
                tmpls.append(med)
                depths.append(max(float(np.median([f[i]["depth"] for f in self._calib])), 5.0))
            if not ok:
                self._calib = []
                return False, "Đang căn chỉnh lại góc mắt...", 1.0
            self.eye_tmpl, self.eye_depth = tmpls, depths

            sc = np.array([self._eye_score(f) for f in self._calib])
            m, sigma = float(sc.mean()), float(sc.std())
            self.closed_th = m - max(0.16, 3.5 * sigma)
            self.open_th = m - max(0.08, 1.8 * sigma)
            self._calib = []
            return False, ("Vui lòng chớp mắt 1 lần" if target_blinks == 1 else "Vui lòng chớp mắt 2 lần"), 1.0

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
            return True, f"Đã chớp mắt đủ {target_blinks} lần!", score

        return False, ("Vui lòng chớp mắt 1 lần" if target_blinks == 1 else f"Đã chớp ({self.blink_count}/{target_blinks}), vui lòng chớp thêm..."), score

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
            return False, "Đang tìm khuôn mặt...", 0.0

        ratio = mouth_dist / eye_dist
        lift_d = ((rm[1] + lm[1]) / 2.0 - nose[1]) / eye_dist
        teeth = self._teeth_ratio(frame_bgr, rm, lm) if frame_bgr is not None else 0.0

        if self.baseline_mouth_ratio is None:
            self._base_ratios.append(ratio)
            self._base_lifts.append(lift_d)
            self._base_teeth.append(teeth)
            remain = self.SMILE_BASE_FRAMES - len(self._base_ratios)
            if remain > 0:
                return False, "Vui lòng giữ nét mặt tự nhiên...", ratio
            self.baseline_mouth_ratio = float(np.median(self._base_ratios))
            self.baseline_lift = float(np.median(self._base_lifts))
            self.baseline_teeth = float(np.median(self._base_teeth))
            return False, "Vui lòng mỉm cười nhẹ", ratio

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
                return True, "Đã xác thực nụ cười thành công!", r
            return False, f"Đang cười ({self.smile_frames}/{self.SMILE_MIN_FRAMES})...", r

        self.smile_frames = 0
        if width_gain < 0.02:
            self.baseline_mouth_ratio = 0.98 * self.baseline_mouth_ratio + 0.02 * r
        return False, "Vui lòng mỉm cười nhẹ", r

    # --- 4. GIO 2 NGON TAY (CHỮ V) ---
    def _check_two_fingers(self, frame_bgr: np.ndarray):
        hands = self._get_mp_hands()
        if hands is None:
            return False, "Chưa khởi tạo được bộ nhận diện bàn tay", []

        h, w = frame_bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))

        try:
            with suppress_c_stderr():
                res = hands.process(rgb)
        except Exception:
            return False, "Đang xử lý cử chỉ bàn tay...", []

        if not res or not res.multi_hand_landmarks:
            self.hand_match_frames = max(0, self.hand_match_frames - 1)
            return False, "Vui lòng giơ ngón tay chữ V vào khung hình", []

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
                return True, "Đã xác nhận cử chỉ chữ V!", v_tips
            return False, f"Giữ nguyên chữ V ({self.hand_match_frames}/{self.FINGER_HOLD_FRAMES})...", v_tips
        else:
            self.hand_match_frames = max(0, self.hand_match_frames - 1)
            return False, "Vui lòng giơ ngón tay chữ V", []

    def check_action(
        self,
        frame_bgr: np.ndarray,
        pose: tuple[float, float, float] | None,
        landmarks: np.ndarray | None,
        face_box: tuple | None
    ) -> tuple[bool, str, dict]:
        debug_info = {"fingertips": []}

        if self.current_action is None:
            return False, "Đang chuẩn bị thử thách...", debug_info

        now = time.time()
        if now - self.challenge_start_time > self.timeout_sec:
            return False, "Hết thời gian thử thách! Vui lòng thử lại.", debug_info

        act = self.current_action

        if act in [ActionType.TURN_LEFT, ActionType.TURN_RIGHT, ActionType.NOD_DOWN, ActionType.LOOK_UP]:
            passed, hint = self._check_pose(pose, act)
            return passed, hint, debug_info

        if act in (ActionType.BLINK_ONCE, ActionType.BLINK_TWICE):
            if landmarks is None:
                return False, "Đang tìm khuôn mặt...", debug_info
            target = 1 if act == ActionType.BLINK_ONCE else 2
            passed, hint, _ = self._check_blink_template(frame_bgr, landmarks, target_blinks=target)
            return passed, hint, debug_info

        if act == ActionType.SMILE:
            if landmarks is None:
                return False, "Đang tìm khuôn mặt...", debug_info
            passed, hint, _ = self._check_smile_adaptive(landmarks, frame_bgr)
            return passed, hint, debug_info

        if act == ActionType.TWO_FINGERS:
            if face_box is None:
                return False, "Vui lòng giữ khuôn mặt trong khung hình khi giơ tay", debug_info
            passed, hint, tips = self._check_two_fingers(frame_bgr)
            debug_info["fingertips"] = tips
            return passed, hint, debug_info

        return False, "Đang chờ...", debug_info