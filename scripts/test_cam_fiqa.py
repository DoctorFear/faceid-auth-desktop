import os
import sys
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)
sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor

def main():
    detector = YuNetDetector()
    fiqa = FaceQualityAssessor()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[-] Khong the mo webcam.")
        return

    print("[*] Bat dau test YuNet + FIQA. Nhan 'q' de thoat.")

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        detections = detector.detect(frame)

        for box, landmarks, score in detections:
            top, right, bottom, left = box
            
            # 1. Ve Box mat va diem tin cay
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.putText(frame, f"Conf: {score:.2f}", (left, max(20, top - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

            # 2. Ve 5 diem landmarks
            for pt in landmarks:
                cv2.circle(frame, (int(pt[0]), int(pt[1])), 4, (0, 0, 255), -1)

            # 3. Kiem tra chat luong anh (Do net, do sang, kich thuoc)
            quality_err = fiqa.check_quality(frame, box)
            
            # 4. Tinh toan goc quay dau (Head Pose)
            pose = fiqa.estimate_pose(landmarks, w, h, yaw_sign=1, pitch_sign=-1)

            # Hien thi thong so len man hinh
            y_offset = bottom + 20
            if pose is not None:
                yaw, pitch, roll = pose
                pose_str = f"Yaw:{yaw:+.1f} | Pitch:{pitch:+.1f} | Roll:{roll:+.1f}"
                cv2.putText(frame, pose_str, (left, min(h - 10, y_offset)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
                y_offset += 20

            if quality_err:
                cv2.putText(frame, f"Quality: {quality_err}", (left, min(h - 10, y_offset)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            else:
                cv2.putText(frame, "Quality: OK", (left, min(h - 10, y_offset)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        cv2.imshow("Test YuNet + FIQA Pose", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
