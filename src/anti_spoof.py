import os
from collections import defaultdict, deque

import cv2
import numpy as np
import onnxruntime as ort


class MiniFASNetDetector:
    """MiniFASNet (Silent-Face) anti-spoofing: input BGR, thang 0-255, class 1 = real."""

    def __init__(self, model_path=None, real_threshold=0.60, scale=2.7,
                 window=5, min_face=80, blur_min=None):
        if model_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, "models", "minifasnet_v2.onnx")

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"[-] Khong tim thay model tai: {model_path}")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name

        # Doc kich thuoc input tu model, mac dinh 80x80 neu la dynamic axis
        h, w = inp.shape[2], inp.shape[3]
        self.out_size = (w if isinstance(w, int) else 80,
                         h if isinstance(h, int) else 80)

        self.real_threshold = real_threshold
        self.scale = scale
        self.min_face = min_face      # mat nho hon (px) thi khong tin ket qua
        self.blur_min = blur_min      # None = tat; vd 15.0 de loai anh qua mo
        self.window = window
        self._history = defaultdict(lambda: deque(maxlen=self.window))
        self.last_reason = ""

    # ------------------------------------------------------------------ crop
    def _crop_with_scale(self, image, box):
        top, right, bottom, left = box
        img_h, img_w = image.shape[:2]
        w, h = right - left, bottom - top

        scale = min((img_h - 1) / h, (img_w - 1) / w, self.scale)
        new_w, new_h = w * scale, h * scale
        cx, cy = left + w / 2.0, top + h / 2.0

        x1, y1 = cx - new_w / 2, cy - new_h / 2
        x2, y2 = cx + new_w / 2, cy + new_h / 2

        # Dich vung crop vao trong anh de giu ti le, khong cat cut
        if x1 < 0: x2 -= x1; x1 = 0
        if y1 < 0: y2 -= y1; y1 = 0
        if x2 > img_w - 1: x1 -= x2 - (img_w - 1); x2 = img_w - 1
        if y2 > img_h - 1: y1 -= y2 - (img_h - 1); y2 = img_h - 1

        x1, y1 = max(0, int(x1)), max(0, int(y1))
        cropped = image[y1:int(y2) + 1, x1:int(x2) + 1]
        return cv2.resize(cropped, self.out_size)

    # --------------------------------------------------------------- predict
    def _valid_box(self, image, box):
        top, right, bottom, left = box
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            self.last_reason = "box khong hop le"
            return False
        if min(w, h) < self.min_face:
            self.last_reason = "mat qua nho"
            return False
        if self.blur_min is not None:
            t, b = max(0, top), min(image.shape[0], bottom)
            l, r = max(0, left), min(image.shape[1], right)
            gray = cv2.cvtColor(image[t:b, l:r], cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray, cv2.CV_64F).var() < self.blur_min:
                self.last_reason = "anh qua mo"
                return False
        return True

    def predict(self, bgr_image, box):
        """Du doan 1 frame. Tra ve (is_real, real_score, probs)."""
        crop_bgr = self._crop_with_scale(bgr_image, box)

        blob = crop_bgr.astype(np.float32)          # BGR, 0-255, KHONG chia 255
        blob = np.expand_dims(np.transpose(blob, (2, 0, 1)), axis=0)

        logits = self.session.run(None, {self.input_name: blob})[0][0]
        exp_vals = np.exp(logits - np.max(logits))
        probs = exp_vals / np.sum(exp_vals)

        real_score = float(probs[1]) if len(probs) > 1 else float(probs[0])
        return real_score >= self.real_threshold, real_score, probs

    def predict_smoothed(self, bgr_image, box, track_id=0):
        """Trung binh probs qua `window` frame gan nhat, on dinh hon predict().

        - Chua du `window` frame thi tra ve is_real=False (dang cho).
        - Mat qua nho / qua mo thi bi tu choi va xoa lich su.
        - track_id: dung khi co nhieu mat de khong tron lich su voi nhau.
        """
        if not self._valid_box(bgr_image, box):
            self._history.pop(track_id, None)
            return False, 0.0, np.zeros(3)

        _, _, probs = self.predict(bgr_image, box)
        hist = self._history[track_id]
        hist.append(probs)

        mean_probs = np.mean(hist, axis=0)
        real_score = float(mean_probs[1])

        if len(hist) < self.window:
            self.last_reason = "dang thu thap frame"
            return False, real_score, mean_probs

        self.last_reason = ""
        return real_score >= self.real_threshold, real_score, mean_probs

    def reset(self, track_id=None):
        """Xoa lich su khi mat roi khung hinh hoac doi nguoi."""
        if track_id is None:
            self._history.clear()
        else:
            self._history.pop(track_id, None)
