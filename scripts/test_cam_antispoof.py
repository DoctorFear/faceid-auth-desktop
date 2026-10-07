import os
import sys
import cv2

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)
sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.anti_spoof import MiniFASNetDetector

def main():
    detector = YuNetDetector()
    anti = MiniFASNetDetector(real_threshold=0.60)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[-] Khong the mo webcam.")
        return

    print("[*] Bat dau test Anti-Spoof (MiniFASNetV2). Nhan 'q' de thoat.")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        frame = cv2.flip(frame, 1)
        detections = detector.detect(frame)

        for box, _, _ in detections:
            top, right, bottom, left = box
            
            # Du doan nguoi that hay gia mao
            is_real, score, probs = anti.predict_smoothed(frame, box)

            # probs: [Print, Real, Screen]
            p_print, p_real, p_screen = probs

            if is_real:
                color = (0, 255, 0)
                label = f"REAL ({int(score * 100)}%)"
            else:
                color = (0, 0, 255)
                spoof_type = "SCREEN" if p_screen >= p_print else "PRINT"
                label = f"SPOOF [{spoof_type}] ({int((1 - score) * 100)}%)"

            # Ve khung va xac suat tung lop
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(frame, label, (left, max(25, top - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            detail = f"Real:{p_real:.2f} | Screen:{p_screen:.2f} | Print:{p_print:.2f}"
            cv2.putText(frame, detail, (left, min(frame.shape[0] - 10, bottom + 25)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

        cv2.imshow("Test Anti-Spoof Live", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
