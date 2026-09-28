import cv2
import face_recognition
import numpy as np
import pickle
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "faces.pickle")

STEPS = [
    {"name": "THANG", "title": "BUOC 1/5: NHIN THANG", "target": "center", "guide": "Nhin truc dien vao camera"},
    {"name": "TRAI",  "title": "BUOC 2/5: QUAY TRAI",  "target": "left",   "guide": "Quay mat sang TRAI <--"},
    {"name": "PHAI",  "title": "BUOC 3/5: QUAY PHAI",  "target": "right",  "guide": "Quay mat sang PHAI -->"},
    {"name": "NGANG", "title": "BUOC 4/5: NGANG LEN",  "target": "up",     "guide": "Hoi nang cam len [^]"},
    {"name": "CUI",   "title": "BUOC 5/5: CUI XUONG",  "target": "down",   "guide": "Hoi cui cam xuong [v]"}
]

def load_face_data():
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return {}

def save_face_data(data):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "wb") as f:
        pickle.dump(data, f)
    print(f"\n[+] Da luu du lieu vao {DATA_PATH}")

def calculate_pose_2d(landmarks, box):
    """
    Do goc quay bang ti le 2D truc quan (khong so bi anh huong boi kinh hay webcam ao)
    """
    top, right, bottom, left = box
    nose = landmarks['nose_bridge'][-1] # Dau mui
    left_jaw = landmarks['chin'][0]     # Me trai
    right_jaw = landmarks['chin'][16]   # Me phai

    d_left = abs(nose[0] - left_jaw[0])
    d_right = abs(nose[0] - right_jaw[0])
    total_w = d_left + d_right
    if total_w == 0:
        return 0, 0

    # Yaw_ratio: ~0.5 la giua, <0.4 quay trai, >0.6 quay phai
    yaw_ratio = d_left / total_w

    # Pitch_ratio: Vi tri mui so voi chieu cao mat
    face_h = bottom - top
    pitch_ratio = (nose[1] - top) / face_h if face_h > 0 else 0.5

    return yaw_ratio, pitch_ratio

def get_alignment_percent(yaw_ratio, pitch_ratio, target):
    """Quy doi thanh diem % ro rang tu 0% den 100%"""
    if target == "center":
        dev = abs(yaw_ratio - 0.5) + abs(pitch_ratio - 0.55)
        score = int(max(0, 100 - (dev * 220)))
        return min(100, score)
    elif target == "left":
        # Quay trai: ti le yaw_ratio giam xuong (< 0.42)
        score = int(max(0, min(100, (0.50 - yaw_ratio) * 450 + 40)))
        return score
    elif target == "right":
        # Quay phai: ti le yaw_ratio tang len (> 0.58)
        score = int(max(0, min(100, (yaw_ratio - 0.50) * 450 + 40)))
        return score
    elif target == "up":
        # Ngang len: mui dich gan ve mep tren (pitch_ratio giam)
        score = int(max(0, min(100, (0.58 - pitch_ratio) * 400 + 40)))
        return score
    elif target == "down":
        # Cui xuong: mui dich ve mep duoi (pitch_ratio tang)
        score = int(max(0, min(100, (pitch_ratio - 0.52) * 400 + 40)))
        return score
    return 0

def enroll_via_webcam(username):
    print(f"[*] Bat dau dang ky: {username}")
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[-] Khong mo duoc Camera.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    face_db = load_face_data()
    user_encodings = []

    step_idx = 0
    hold_start_time = None
    REQUIRED_HOLD_SEC = 0.35

    while step_idx < len(STEPS):
        ret, frame = cap.read()
        if not ret:
            break

        display_frame = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)

        boxes = face_recognition.face_locations(rgb_frame, model="hog")
        current_step = STEPS[step_idx]

        percent = 0
        box_color = (0, 0, 255) # Do
        is_ready = False
        hold_pct = 0

        if len(boxes) == 1:
            box = boxes[0]
            top, right, bottom, left = box
            
            # Lay landmarks truc tiep
            all_landmarks = face_recognition.face_landmarks(rgb_frame, [box])
            if all_landmarks:
                landmarks = all_landmarks[0]
                yaw_r, pitch_r = calculate_pose_2d(landmarks, box)
                percent = get_alignment_percent(yaw_r, pitch_r, current_step["target"])

                if percent >= 75:
                    box_color = (0, 255, 0) # Xanh la
                    if hold_start_time is None:
                        hold_start_time = time.time()
                    elapsed = time.time() - hold_start_time
                    hold_pct = int(min(1.0, elapsed / REQUIRED_HOLD_SEC) * 100)
                    if elapsed >= REQUIRED_HOLD_SEC:
                        is_ready = True
                elif percent >= 50:
                    box_color = (0, 200, 255) # Vang
                    hold_start_time = None
                else:
                    box_color = (0, 0, 255) # Do
                    hold_start_time = None

            cv2.rectangle(display_frame, (left, top), (right, bottom), box_color, 2)
        else:
            hold_start_time = None

        # --- GIAO DIEN HIEN THI % TO RO ---
        # 1. Bang dieu khien tren cung
        cv2.rectangle(display_frame, (0, 0), (640, 75), (25, 25, 25), -1)
        cv2.putText(display_frame, current_step["title"], (15, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(display_frame, current_step["guide"], (15, 60), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

        # 2. Dong ho % goc khop o goc phai (luon hien thi)
        score_text = f"{percent}%"
        cv2.rectangle(display_frame, (510, 8), (625, 67), (40, 40, 40), -1)
        cv2.putText(display_frame, score_text, (525, 52), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, box_color, 3)

        # 3. Thanh tien trinh giu yen tu chup
        cv2.rectangle(display_frame, (0, 75), (640, 82), (40, 40, 40), -1)
        if hold_pct > 0:
            bw = int(640 * (hold_pct / 100.0))
            cv2.rectangle(display_frame, (0, 75), (bw, 82), (0, 255, 0), -1)

        # 4. Huong dan duoi chan
        cv2.rectangle(display_frame, (0, 445), (640, 480), (20, 20, 20), -1)
        cv2.putText(display_frame, "Dat >= 75% he thong TU CHUP | SPACE: Chup ngay | Q: Thoat", 
                    (20, 468), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (180, 180, 180), 1)

        cv2.imshow("Smart Face Enrollment", display_frame)

        key = cv2.waitKey(1) & 0xFF
        if (is_ready or key == ord(' ')) and len(boxes) == 1:
            enc = face_recognition.face_encodings(rgb_frame, [boxes[0]])
            if enc:
                user_encodings.append(enc[0])
                print(f"[✓] Chup thanh cong: {current_step['name']} ({step_idx + 1}/5) - Do chuan: {percent}%")
                step_idx += 1
                hold_start_time = None
                time.sleep(0.25)

        if key == ord('q'):
            print("[-] Nguoi dung huy.")
            break

    cap.release()
    cv2.destroyAllWindows()

    if step_idx == len(STEPS):
        face_db[username] = user_encodings
        save_face_data(face_db)
        print(f"\n[★] THANH CONG: Da cap nhat 5 goc cho '{username}'!")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else input("Username: ").strip()
    enroll_via_webcam(target)
