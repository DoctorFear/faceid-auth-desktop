import os
import sys
import time
import traceback

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["GLOG_minloglevel"] = "2"

import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR in sys.path:
    sys.path.remove(BASE_DIR)
sys.path.append(BASE_DIR)

print("[DEBUG 1/7] Dang import cac module loi...")
from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.liveness import ChallengeResponseDetector, ActionType
from core.fusion import MultiModalFusionEngine

EMB_DIR = os.path.join(BASE_DIR, "data", "embeddings")

def load_templates():
    db = {}
    if not os.path.exists(EMB_DIR):
        return db
    for f in os.listdir(EMB_DIR):
        if f.endswith(".npy"):
            user = f[:-4]
            db[user] = np.load(os.path.join(EMB_DIR, f))
    return db

def main():
    try:
        print("[DEBUG 2/7] Dang ket noi webcam...")
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[-] LOI: Khong the mo webcam.")
            return

        ret, test_frame = cap.read()
        if not ret or test_frame is None:
            print("[-] LOI: Khong the doc frame test tu camera.")
            cap.release()
            return
        print(f"[*] Webcam OK: {test_frame.shape[1]}x{test_frame.shape[0]}")

        print("[DEBUG 3/7] Dang load database embedding...")
        db = load_templates()
        print(f"[*] Da load {len(db)} tai khoan: {list(db.keys())}")

        print("[DEBUG 4/7] Dang khoi tao YuNet Detector...")
        t0 = time.time()
        detector = YuNetDetector()
        print(f"[*] YuNet xong ({int((time.time()-t0)*1000)}ms)")

        print("[DEBUG 5/7] Dang khoi tao ArcFace Embedder...")
        t0 = time.time()
        embedder = ArcFaceEmbedder()
        print(f"[*] ArcFace xong ({int((time.time()-t0)*1000)}ms)")

        print("[DEBUG 6/7] Dang khoi tao MiniFASNet Anti-spoof...")
        t0 = time.time()
        anti_spoof = MiniFASNetDetector(real_threshold=0.60)
        fiqa = FaceQualityAssessor()
        print(f"[*] Anti-spoof & FIQA xong ({int((time.time()-t0)*1000)}ms)")

        print("[DEBUG 7/7] Dang khoi tao Challenge Liveness Engine...")
        challenge = ChallengeResponseDetector(timeout_sec=16.0)
        fusion = MultiModalFusionEngine(sim_threshold=0.62)
        
        # Dat thu thach dau tien la QUAY TRAI de tranh goi TFLite luc mo app
        current_action = challenge.reset_challenge(ActionType.TURN_LEFT)
        print(f"[*] Thu thach khoi diem an toan: {current_action.value}")

        active_success = False
        authenticated = False
        auth_user = ""
        auth_sim = 0.0

        cv2.namedWindow("FaceID Multi-Modal Pipeline", cv2.WINDOW_AUTOSIZE)
        print("\n>>> DA VAO CAMERA LOOP THANH CONG. Cua so hien thi dang chay! <<<")

        frame_idx = 0
        fps_t0 = time.time()

        while True:
            t_start = time.time()
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                print("[-] Canh bao: Frame rong")
                continue

            frame_idx += 1
            h, w = frame.shape[:2]

            # 1. Detect
            t_det = time.time()
            detections = detector.detect(frame)
            ms_det = int((time.time() - t_det) * 1000)

            status_text = f"THU THACH: {current_action.value}" if not active_success else "THU THACH OK -> DANG XAC THUC..."
            detail_text = ""
            box_color = (0, 200, 255)
            box_mirrored = None
            fingertips_draw = []

            ms_liveness = 0
            ms_embed = 0

            if authenticated:
                box_color = (0, 255, 0)
                status_text = f"XAC THUC THANH CONG: {auth_user} ({auth_sim*100:.1f}%)"
                detail_text = "He thong da mo khoa! Nhan 'r' de thu lai."

            elif len(detections) == 0:
                detail_text = "Dua khuon mat vao khung hinh..."
                if current_action.name == "TWO_FINGERS" and not active_success:
                    t_live = time.time()
                    passed, hint, dbg = challenge.check_action(frame, None, None, None)
                    ms_liveness = int((time.time() - t_live) * 1000)
                    detail_text = hint
                    fingertips_draw = dbg.get("fingertips", [])
                    if passed:
                        active_success = True
            elif len(detections) > 1:
                detail_text = "Canh bao: Chi de 1 nguoi truoc camera"
            else:
                box, landmarks, _ = detections[0]
                top, right, bottom, left = box
                box_mirrored = (w - right, top, w - left, bottom)

                is_real, passive_score, _ = anti_spoof.predict_smoothed(frame, box)
                pose = fiqa.estimate_pose(landmarks, w, h, yaw_sign=1, pitch_sign=-1)

                # Chi kiem tra thu thach Liveness (Khoa chat ArcFace khi chua xong)
                if not active_success:
                    t_live = time.time()
                    passed, hint, dbg = challenge.check_action(frame, pose, landmarks, box)
                    ms_liveness = int((time.time() - t_live) * 1000)
                    detail_text = hint
                    fingertips_draw = dbg.get("fingertips", [])
                    if passed:
                        active_success = True

                # Chi khi xong thu thach moi chay so khop ArcFace
                elif not authenticated:
                    detail_text = "Dang trich xuat vector va so khop danh tinh..."
                    t_emb = time.time()
                    emb = embedder.extract(frame, box)
                    ms_embed = int((time.time() - t_emb) * 1000)

                    best_user = None
                    min_dist = 1.0

                    for u, t_list in db.items():
                        for t in t_list:
                            d = embedder.compute_distance(emb, t)
                            if d < min_dist:
                                min_dist = d
                                best_user = u

                    decision = fusion.evaluate(
                        passive_score=passive_score,
                        active_passed=True,
                        best_user=best_user,
                        min_cosine_dist=min_dist
                    )

                    if decision.success:
                        authenticated = True
                        auth_user = decision.matched_user
                        auth_sim = decision.similarity
                    elif not decision.is_live:
                        box_color = (0, 0, 255)
                        detail_text = f"Tu choi: {decision.reason}"

            # Hiển thị
            display = cv2.flip(frame, 1)

            if box_mirrored is not None:
                x1, y1, x2, y2 = box_mirrored
                cv2.rectangle(display, (x1, y1), (x2, y2), box_color, 2)

            for (fx, fy) in fingertips_draw:
                mirrored_fx = w - fx
                cv2.circle(display, (mirrored_fx, fy), 8, (0, 255, 0), -1)
                cv2.circle(display, (mirrored_fx, fy), 12, (0, 255, 255), 2)

            cv2.rectangle(display, (0, 0), (w, 65), (25, 25, 25), -1)
            cv2.putText(display, status_text, (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(display, detail_text, (15, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

            cv2.rectangle(display, (0, h - 35), (w, h), (15, 15, 15), -1)
            cv2.putText(display, "Nhan 'r': Doi thu thach | 'q': Thoat", (15, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)

            cv2.imshow("FaceID Multi-Modal Pipeline", display)

            # In thong so benchmark theo thoi gian thuc tren terminal
            fps = 1.0 / max(1e-4, time.time() - t_start)
            print(f"[Frame {frame_idx:04d}] FPS: {fps:4.1f} | Det: {ms_det:2d}ms | Live: {ms_liveness:2d}ms | Emb: {ms_embed:2d}ms", end="\r")

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('r'):
                active_success = False
                authenticated = False
                current_action = challenge.reset_challenge()
                print(f"\n[*] Doi thu thach sang: {current_action.value}")

    except Exception as ex:
        print("\n[-] Xay ra ngoai le:")
        traceback.print_exc()
    finally:
        print("\n[*] Dang giai phong tai nguyen...")
        cap.release()
        cv2.destroyAllWindows()
        print("[*] Hoan tat tat he thong.")

if __name__ == "__main__":
    main()
