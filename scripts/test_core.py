import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)
sys.path.append(BASE_DIR)

import numpy as np
from core.detector import YuNetDetector
from core.embedder import ArcFaceEmbedder
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector

print("[*] 1. Kiem tra ArcFace Embedder...")
embedder = ArcFaceEmbedder()
dummy = np.zeros((480, 640, 3), dtype=np.uint8)
box = (100, 300, 300, 100) # top, right, bottom, left
emb = embedder.extract(dummy, box)
assert emb.shape == (512,), f"ArcFace sai shape: {emb.shape}"
print("    --> OK (Vector 512-d)")

print("[*] 2. Kiem tra FIQA & Pose Estimation...")
fiqa = FaceQualityAssessor()
landmarks = np.array([[150, 150], [250, 150], [200, 200], [170, 260], [230, 260]], dtype=np.float32)
pose = fiqa.estimate_pose(landmarks, 640, 480)
assert pose is not None, "FIQA loi khong tinh duoc pose"
yaw, pitch, roll = pose
print(f"    --> OK (Pose: yaw={yaw:.1f}, pitch={pitch:.1f}, roll={roll:.1f})")

print("[*] 3. Kiem tra Anti-Spoof (MiniFASNet)...")
anti = MiniFASNetDetector()
is_real, score, probs = anti.predict(dummy, box)
assert len(probs) == 3, "MiniFASNet sai shape xac suat"
print(f"    --> OK (Probs: {probs})")

print("\n[✓] TAT CA CAC MODULE TRONG CORE DEU HOAT DONG DUNG CHUAN!")
