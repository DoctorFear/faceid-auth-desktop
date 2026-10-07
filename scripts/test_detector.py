import os
import sys
import cv2

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)
sys.path.append(BASE_DIR)

from core.detector import YuNetDetector

def main():
    print("[*] Khoi tao YuNet Detector...")
    detector = YuNetDetector()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[-] Khong the mo webcam.")
        return

    print("[*] Dang chay test Detector. Nhan 'q' de thoat.")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        frame = cv2.flip(frame, 1)
        detections = detector.detect(frame)

        for box, landmarks, score in detections:
            top, right, bottom, left = box

            # 1. Ve Bounding Box khuon mat
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.putText(frame, f"Face: {score:.2f}", (left, max(20, top - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # 2. Ve 5 diem moc: 2 mat (do), mui (xanh la), 2 mieng (xanh duong)
            colors = [(0, 0, 255), (0, 0, 255), (0, 255, 0), (255, 0, 0), (255, 0, 0)]
            for (x, y), color in zip(landmarks, colors):
                cv2.circle(frame, (int(x), int(y)), 4, color, -1)

        cv2.putText(frame, f"Faces detected: {len(detections)}", (15, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        cv2.imshow("Test YuNet Detector Only", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
