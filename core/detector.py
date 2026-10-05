import os
import cv2
import numpy as np

class YuNetDetector:
    def __init__(self, model_path=None, conf_threshold=0.75, nms_threshold=0.3):
        if model_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, "models", "face_detection_yunet.onnx")

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"[-] Khong tim thay model YuNet: {model_path}")

        self.detector = cv2.FaceDetectorYN.create(
            model_path, "", (320, 320),
            score_threshold=conf_threshold,
            nms_threshold=nms_threshold,
            top_k=5000
        )

    def detect(self, frame_bgr: np.ndarray):
        """
        Phat hien khuon mat.
        Tra ve list cac tuple: (box, landmarks, score)
        - box: (top, right, bottom, left)
        - landmarks: array 5 diem shape (5, 2)
        - score: float do tin cay
        """
        h, w = frame_bgr.shape[:2]
        self.detector.setInputSize((w, h))

        _, faces = self.detector.detect(frame_bgr)
        results = []
        if faces is None:
            return results

        for face in faces:
            x, y, fw, fh = map(int, face[:4])
            score = float(face[-1])
            landmarks = face[4:14].reshape((5, 2))

            top = max(0, y)
            left = max(0, x)
            bottom = min(h, y + fh)
            right = min(w, x + fw)

            box = (top, right, bottom, left)
            results.append((box, landmarks, score))

        return results
