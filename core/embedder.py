import os
import cv2
import numpy as np
import onnxruntime as ort

# 5 toa do moc InsightFace/ArcFace chuan tren khung hinh 112x112:
# Mat phai, Mat trai, Mui, Khoe mieng phai, Khoe mieng trai
ARCFACE_STANDARD_LANDMARKS_112 = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041]
], dtype=np.float32)


def align_face_5pts(frame_bgr: np.ndarray, landmarks: np.ndarray, output_size: tuple[int, int] = (112, 112)) -> np.ndarray:
    """Can chinh khuon mat dua tren 5 diem moc bang Similarity Transform (xoay, co gian, tinh tien)."""
    src_pts = np.asarray(landmarks, dtype=np.float32)
    dst_pts = ARCFACE_STANDARD_LANDMARKS_112

    transform_matrix, _ = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.LMEDS)

    if transform_matrix is None:
        return cv2.resize(frame_bgr, output_size)

    warped = cv2.warpAffine(
        frame_bgr,
        transform_matrix,
        output_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101
    )
    return warped


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

    def preprocess(self, face_bgr_112: np.ndarray) -> np.ndarray:
        img = cv2.cvtColor(face_bgr_112, cv2.COLOR_BGR2RGB).astype(np.float32)
        img = (img - 127.5) / 128.0

        if len(self.input_shape) == 4 and self.input_shape[1] == 3:
            img = np.transpose(img, (2, 0, 1))

        return np.expand_dims(img, axis=0)

    def extract(self, frame_bgr: np.ndarray, box: tuple, landmarks: np.ndarray | None = None) -> np.ndarray:
        """Trich xuat vector 512-d ArcFace. Tu dong can chinh khuon mat neu co landmarks."""
        top, right, bottom, left = box

        if landmarks is not None and len(landmarks) >= 5:
            aligned_face = align_face_5pts(frame_bgr, landmarks[:5], output_size=(112, 112))
        else:
            crop = frame_bgr[max(0, top):min(frame_bgr.shape[0], bottom),
                             max(0, left):min(frame_bgr.shape[1], right)]
            if crop.size == 0:
                return np.zeros(512, dtype=np.float32)
            aligned_face = cv2.resize(crop, (112, 112))

        blob = self.preprocess(aligned_face)
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