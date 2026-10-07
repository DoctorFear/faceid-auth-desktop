import os
import sys
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)
sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.embedder import ArcFaceEmbedder

EMB_DIR = os.path.join(BASE_DIR, "data", "embeddings")

def load_user(username: str):
    path = os.path.join(EMB_DIR, f"{username}.npy")
    if not os.path.exists(path):
        return None
    return np.load(path)

def main():
    username = "testuser"
    user_embeddings = load_user(username)
    if user_embeddings is None:
        print(f"[-] Khong tim thay file {username}.npy trong data/embeddings/")
        return

    print(f"[✓] Da load {len(user_embeddings)} vector cua {username}")
    detector = YuNetDetector()
    embedder = ArcFaceEmbedder()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[-] Khong the mo webcam.")
        return

    print("[*] Bat dau test ArcFace. Nhan 'q' de thoat.")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        frame = cv2.flip(frame, 1)
        detections = detector.detect(frame)

        for box, _, _ in detections:
            top, right, bottom, left = box

            # Trich xuat vector 512-d
            emb = embedder.extract(frame, box)

            # Tinh khoang cach Cosine nho nhat voi 5 vector trong DB
            min_dist = min([embedder.compute_distance(emb, t) for t in user_embeddings])
            similarity = max(0.0, (1.0 - min_dist) * 100)

            # Nguong chap nhan thuong <= 0.36
            if min_dist < 0.36:
                color = (0, 255, 0)
                text = f"{username} ({similarity:.1f}%)"
            else:
                color = (0, 0, 255)
                text = f"Unknown (Dist: {min_dist:.2f})"

            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(frame, text, (left, max(25, top - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

        cv2.imshow("Test ArcFace Matching", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
