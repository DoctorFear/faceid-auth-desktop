import os
import cv2
import numpy as np
import onnxruntime as ort

class ArcFaceEmbedder:
    def __init__(self, model_path=None):
        if model_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, "models", "arcface.onnx")

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"[-] Khong tim thay model ArcFace tai: {model_path}")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape

    def preprocess(self, face_bgr: np.ndarray) -> np.ndarray:
        face_resized = cv2.resize(face_bgr, (112, 112))
        img = cv2.cvtColor(face_resized, cv2.COLOR_BGR2RGB).astype(np.float32)
        img = (img - 127.5) / 128.0

        # Tuong thich dinh dang NCHW (1, 3, 112, 112) hoac NHWC (1, 112, 112, 3)
        if len(self.input_shape) == 4 and self.input_shape[1] == 3:
            img = np.transpose(img, (2, 0, 1))

        return np.expand_dims(img, axis=0)

    def extract(self, frame_bgr: np.ndarray, box: tuple) -> np.ndarray:
        top, right, bottom, left = box
        crop = frame_bgr[max(0, top):min(frame_bgr.shape[0], bottom),
                         max(0, left):min(frame_bgr.shape[1], right)]
        if crop.size == 0:
            return np.zeros(512, dtype=np.float32)

        blob = self.preprocess(crop)
        embeddings = self.session.run([self.output_name], {self.input_name: blob})[0][0]

        # L2 Normalize
        norm = np.linalg.norm(embeddings)
        if norm > 0:
            embeddings = embeddings / norm
        return embeddings.astype(np.float32)

    @staticmethod
    def compute_distance(emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Khoang cach Cosine (0: giong nhau hoan toan, 1: khac biet)."""
        dot = float(np.dot(emb1, emb2))
        return max(0.0, 1.0 - dot)
