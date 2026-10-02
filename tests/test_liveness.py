import cv2
import face_recognition
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from src.liveness import BlinkDetector

CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")
ear_thresh = 0.21
consec_frames = 2
camera_idx = 0

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            ear_thresh = cfg.get("ear_threshold", 0.21)
            consec_frames = cfg.get("consecutive_frames", 2)
            camera_idx = cfg.get("camera_id", 0)
    except Exception:
        pass

detector = BlinkDetector(ear_threshold=ear_thresh, consecutive_frames=consec_frames)

print(f"[*] Dang mo Camera {camera_idx}...")
cap = cv2.VideoCapture(camera_idx)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print(f"[-] Khong mo duoc Camera {camera_idx}. Hay kiem tra Devices -> Webcams tren VirtualBox.")
    sys.exit(1)

# Thời gian khởi động camera máy ảo và loại bỏ frame tối ban đầu
time.sleep(1.0)
for _ in range(5):
    cap.read()

print(f"[*] San sang test Liveness! (EAR Threshold: {ear_thresh}, Consecutive Frames: {consec_frames})")
print("[*] Nhan 'q' de thoat.")

consecutive_failures = 0

while True:
    ret, frame = cap.read()
    if not ret or frame is None:
        consecutive_failures += 1
        if consecutive_failures > 30:
            print("[-] Mat luong video tu camera.")
            break
        time.sleep(0.05)
        continue

    consecutive_failures = 0

    frame = cv2.flip(frame, 1)
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    landmarks_list = face_recognition.face_landmarks(rgb_frame)

    current_ear = 0.0
    status_str = "DANG CHO KHUON MAT..."
    box_color = (0, 0, 255)

    if landmarks_list:
        landmarks = landmarks_list[0]
        is_blink, current_ear = detector.update(landmarks)

        # Chấm điểm mốc quanh 2 mắt
        if "left_eye" in landmarks and "right_eye" in landmarks:
            for pt in landmarks["left_eye"] + landmarks["right_eye"]:
                cv2.circle(frame, pt, 2, (0, 255, 255), -1)

        if current_ear < ear_thresh:
            status_str = "MAT: DANG NHAM"
            box_color = (0, 165, 255)
        else:
            status_str = "MAT: DANG MO"
            box_color = (0, 255, 0)

    # Hiển thị thông số
    cv2.rectangle(frame, (0, 0), (640, 70), (30, 30, 30), -1)
    cv2.putText(frame, f"EAR: {current_ear:.3f} (Nguong: {ear_thresh})", (15, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(frame, f"Trang thai: {status_str} | Chop mat: {detector.blink_counter}", (15, 55),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, box_color, 2)

    cv2.imshow("Test Anti-Spoofing - Blink Detection", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
