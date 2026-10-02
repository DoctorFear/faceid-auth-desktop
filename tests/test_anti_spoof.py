import os
import sys
import time
import json
import cv2
import face_recognition

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from src.anti_spoof import MiniFASNetDetector

CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")
camera_idx = 0

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            camera_idx = cfg.get("camera_id", 0)
    except Exception:
        pass

print("[*] Dang khoi tao MiniFASNet...")
detector = MiniFASNetDetector(real_threshold=0.60)

print(f"[*] Dang ket noi Camera {camera_idx}...")
cap = cv2.VideoCapture(camera_idx, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print(f"[-] Khong mo duoc Camera {camera_idx}.")
    sys.exit(1)

time.sleep(1.0)
for _ in range(3):
    cap.read()

print("[*] San sang test! Nhan 'q' de thoat.")

while True:
    ret, frame = cap.read()
    if not ret or frame is None:
        continue

    frame = cv2.flip(frame, 1)
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    small_rgb = cv2.resize(rgb_frame, (0, 0), fx=0.5, fy=0.5)

    boxes = face_recognition.face_locations(small_rgb, model="hog")

    for box in boxes:
        top, right, bottom, left = [coord * 2 for coord in box]
        full_box = (top, right, bottom, left)

        is_real, real_score, probs = detector.predict(frame, full_box)

        color = (0, 255, 0) if is_real else (0, 0, 255)
        label = f"REAL: {real_score*100:.1f}%" if is_real else f"FAKE: {real_score*100:.1f}%"

        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.rectangle(frame, (left, top - 26), (right, top), color, -1)
        cv2.putText(frame, label, (left + 5, top - 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # Bang thong so 3 lop thoi gian thuc tren man hinh
        cv2.rectangle(frame, (10, 10), (280, 105), (30, 30, 30), -1)
        cv2.putText(frame, f"Class 0 (Print) : {probs[0]*100:.1f}%", (15, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
        cv2.putText(frame, f"Class 1 (Real)  : {probs[1]*100:.1f}%", (15, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        cv2.putText(frame, f"Class 2 (Screen): {probs[2]*100:.1f}%", (15, 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

    cv2.imshow("Test Anti-Spoofing (MiniFASNet)", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
