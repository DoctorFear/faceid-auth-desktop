import os
from collections import defaultdict, deque
import cv2
import numpy as np
import onnxruntime as ort


class MiniFASNetDetector:
    """MiniFASNet (Silent-Face) anti-spoofing: input BGR, scale 0-255, Class 1 = Real."""

    def __init__(
        self,
        model_path: str | None = None,
        real_threshold: float = 0.60,
        scale: float = 2.7,
        window: int = 5,
        min_face: int = 80,
        blur_min: float | None = None,
    ):
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

        h, w = inp.shape[2], inp.shape[3]
        self.out_size = (
            w if isinstance(w, int) else 80,
            h if isinstance(h, int) else 80,
        )

        self.real_threshold = real_threshold
        self.scale = scale
        self.min_face = min_face
        self.blur_min = blur_min
        self.window = window
        self._history = defaultdict(lambda: deque(maxlen=self.window))
        self.last_reason = ""

    def _crop_with_scale(self, image: np.ndarray, box: tuple) -> np.ndarray:
        top, right, bottom, left = box
        img_h, img_w = image.shape[:2]
        w, h = right - left, bottom - top

        scale = min((img_h - 1) / float(h), (img_w - 1) / float(w), self.scale)
        new_w, new_h = w * scale, h * scale
        cx, cy = left + w / 2.0, top + h / 2.0

        x1, y1 = cx - new_w / 2, cy - new_h / 2
        x2, y2 = cx + new_w / 2, cy + new_h / 2

        if x1 < 0:
            x2 -= x1
            x1 = 0
        if y1 < 0:
            y2 -= y1
            y1 = 0
        if x2 > img_w - 1:
            x1 -= x2 - (img_w - 1)
            x2 = img_w - 1
        if y2 > img_h - 1:
            y1 -= y2 - (img_h - 1)
            y2 = img_h - 1

        x1, y1 = max(0, int(x1)), max(0, int(y1))
        cropped = image[y1 : int(y2) + 1, x1 : int(x2) + 1]
        return cv2.resize(cropped, self.out_size)

    def _valid_box(self, image: np.ndarray, box: tuple) -> bool:
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

    def predict(self, bgr_image: np.ndarray, box: tuple) -> tuple[bool, float, np.ndarray]:
        """Du doan frame don le: tra ve (is_real, real_score, probs)."""
        crop_bgr = self._crop_with_scale(bgr_image, box)
        blob = crop_bgr.astype(np.float32)
        blob = np.expand_dims(np.transpose(blob, (2, 0, 1)), axis=0)

        logits = self.session.run(None, {self.input_name: blob})[0][0]
        exp_vals = np.exp(logits - np.max(logits))
        probs = exp_vals / np.sum(exp_vals)

        # Class 1: Nguoi that, Class 0: In an, Class 2: Man hinh
        real_score = float(probs[1]) if len(probs) > 1 else float(probs[0])
        return real_score >= self.real_threshold, real_score, probs

    def predict_smoothed(
        self, bgr_image: np.ndarray, box: tuple, track_id: int = 0
    ) -> tuple[bool, float, np.ndarray]:
        """Lay trung binh truot qua so luong frame da thiet lap."""
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

    def reset(self, track_id: int | None = None):
        if track_id is None:
            self._history.clear()
        else:
            self._history.pop(track_id, None)