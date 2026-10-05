import os
import sys
import cv2

# Nap thu muc goc vao sys.path de import duoc core
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from core.anti_spoof import MiniFASNetDetector
from core.fiqa import ImageQualityAssessor

print("[*] Khoi tao module Anti-Spoofing & FIQA...")
detector = MiniFASNetDetector(real_threshold=0.60)
fiqa = ImageQualityAssessor()

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("[-] Khong the ket noi den Webcam.")
    sys.exit(1)

print("[?] Webcam san sang. Nhan 'q' de thoat.")

# Su dung CascadeClassifier mac dinh cua OpenCV de test vi tri mat tren Windows
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

while True:
    ret, frame = cap.read()
    if not ret or frame is None:
        continue

    frame = cv2.flip(frame, 1)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))

    for (x, y, w, h) in faces:
        box = (y, x + w, y + h, x) # top, right, bottom, left
        is_good, scores, msg = fiqa.evaluate(frame, box)
        is_real, score, probs = detector.predict_smoothed(frame, box)

        color = (0, 255, 0) if is_real and is_good else (0, 0, 255)
        spoof_type = "Screen" if probs[2] >= probs[0] else "Print"
        status_label = f"REAL: {score*100:.1f}%" if is_real else f"FAKE ({spoof_type}): {(1.0-score)*100:.1f}%"

        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        cv2.putText(frame, status_label, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cv2.putText(frame, f"Quality: {msg}", (x, y + h + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

    cv2.imshow("Test Core Engine (Windows Preview)", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
