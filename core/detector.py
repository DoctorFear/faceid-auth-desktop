import os
import cv2
import numpy as np

# Tat hoan toan dong log [ WARN:0@... Targets are not supported ... ]
cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "models")

def get_yunet_path():
    # Tim file onnx chua chu yunet trong thu muc models
    if os.path.exists(MODELS_DIR):
        for f in os.listdir(MODELS_DIR):
            if f.endswith(".onnx") and "yunet" in f.lower():
                return os.path.join(MODELS_DIR, f)
    # Mac dinh du phong
    return os.path.join(MODELS_DIR, "face_detection_yunet.onnx")

class YuNetDetector:
    def __init__(self, model_path: str = None, conf_threshold: float = 0.6, nms_threshold: float = 0.3):
        if model_path is None:
            model_path = get_yunet_path()

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Khong tim thay file model YuNet tai: {model_path}")

        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.detector = cv2.FaceDetectorYN.create(
            model=model_path,
            config="",
            input_size=(320, 320),
            score_threshold=self.conf_threshold,
            nms_threshold=self.nms_threshold,
            top_k=5000,
            backend_id=cv2.dnn.DNN_BACKEND_OPENCV,
            target_id=cv2.dnn.DNN_TARGET_CPU
        )

    def detect(self, image: np.ndarray):
        h, w = image.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(image)
        if faces is None:
            return []

        results = []
        for face in faces:
            box = (int(face[1]), int(face[0] + face[2]), int(face[1] + face[3]), int(face[0]))
            landmarks = face[4:14].reshape((5, 2))
            score = float(face[14])
            results.append((box, landmarks, score))
        return results
