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
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[-] LOI: Khong the mo webcam.")
            return

        db = load_templates()
        print(f"[*] Da load {len(db)} tai khoan: {list(db.keys())}")

        print("[*] Khoi tao cac module AI (HM 4, 5, 6)...")
        detector = YuNetDetector()
        fiqa = FaceQualityAssessor()
        challenge = ChallengeResponseDetector(timeout_sec=16.0)
        anti_spoof = MiniFASNetDetector(real_threshold=0.60)
        embedder = ArcFaceEmbedder()
        fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.65)

        current_action = challenge.reset_challenge(ActionType.TURN_LEFT)
        active_success = False
        authenticated = False
        auth_user = ""
        auth_conf = 0.0

        cv2.namedWindow("FaceID Multi-Modal Pipeline (HM 4+5+6)", cv2.WINDOW_AUTOSIZE)
        print("\n>>> BANG THEO DOI HOAT DONG MODULE (DIAGNOSTIC LOG) <<<")
        print("Trang thai: [RUN]=Dang chay | [WAIT]=Cho dieu kien | [SKIP]=Bo qua | [PASS]=Dat chuan | [FAIL]=Khong dat\n")

        frame_idx = 0
        last_log_time = time.time()

        while True:
            t_frame_start = time.time()
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                continue

            frame_idx += 1
            h, w = frame.shape[:2]

            # Khoi tao bang trang thai chan doan
            status_diag = {
                "DET": "SKIP",
                "FIQA": "SKIP",
                "FFT": "SKIP",
                "LIVE": "SKIP",
                "PAD": "SKIP",
                "EMB": "WAIT",
                "FUS": "WAIT"
            }
            latencies = {}

            # 1. DETECTOR (YuNet)
            t0 = time.time()
            detections = detector.detect(frame)
            latencies["DET"] = int((time.time() - t0) * 1000)
            status_diag["DET"] = f"RUN({len(detections)})"

            status_text = f"THU THACH: {current_action.value}" if not active_success else "DANG XAC THUC FUSION..."
            detail_text = ""
            box_color = (0, 200, 255)
            box_mirrored = None
            fingertips_draw = []

            if authenticated:
                box_color = (0, 255, 0)
                status_text = f"XAC THUC THANH CONG: {auth_user}"
                detail_text = f"Do tin cay tong hop: {auth_conf*100:.1f}% | Nhan 'r' de thu lai"
                status_diag["EMB"] = "DONE"
                status_diag["FUS"] = "DONE"

            elif len(detections) == 0:
                detail_text = "Dua khuon mat vao khung hinh..."
                if current_action.name == "TWO_FINGERS" and not active_success:
                    t0 = time.time()
                    passed, hint, dbg = challenge.check_action(frame, None, None, None)
                    latencies["LIVE"] = int((time.time() - t0) * 1000)
                    status_diag["LIVE"] = "PASS" if passed else "RUN"
                    detail_text = hint
                    fingertips_draw = dbg.get("fingertips", [])
                    if passed:
                        active_success = True
            elif len(detections) > 1:
                detail_text = "Canh bao: Phat hien nhieu hon 1 khuon mat"
                status_diag["FIQA"] = "FAIL(MultiFace)"
            else:
                box, landmarks, _ = detections[0]
                top, right, bottom, left = box
                box_mirrored = (w - right, top, w - left, bottom)

                # 2. FIQA & FFT MOIRE
                t0 = time.time()
                quality = fiqa.evaluate_quality(frame, box, landmarks)
                latencies["FIQA"] = int((time.time() - t0) * 1000)
                status_diag["FIQA"] = "PASS" if quality.passed else "FAIL"
                status_diag["FFT"] = "PASS" if quality.is_live_fft else "FAIL(Moire)"
                pose = (quality.yaw, quality.pitch, quality.roll)

                # 3. PASSIVE PAD (MiniFASNet)
                t0 = time.time()
                is_real, passive_score, _ = anti_spoof.predict_smoothed(frame, box)
                latencies["PAD"] = int((time.time() - t0) * 1000)
                status_diag["PAD"] = f"RUN({int(passive_score*100)}%)"

                # 4. ACTIVE LIVENESS
                if not active_success:
                    t0 = time.time()
                    passed, hint, dbg = challenge.check_action(frame, pose, landmarks, box)
                    latencies["LIVE"] = int((time.time() - t0) * 1000)
                    status_diag["LIVE"] = "PASS" if passed else "RUN"
                    detail_text = hint
                    fingertips_draw = dbg.get("fingertips", [])
                    if passed:
                        active_success = True
                        status_diag["LIVE"] = "SUCCESS"
                else:
                    status_diag["LIVE"] = "PASSED"

                    # 5. ARCFACE EMBEDDER & FUSION (Chi chay khi Live da dat)
                    if not authenticated:
                        if not quality.passed:
                            box_color = (0, 140, 255)
                            detail_text = f"FIQA: {quality.reason}"
                            status_diag["EMB"] = "WAIT(FIQA)"
                            status_diag["FUS"] = "WAIT(FIQA)"
                        else:
                            t0 = time.time()
                            emb = embedder.extract(frame, box)
                            latencies["EMB"] = int((time.time() - t0) * 1000)
                            status_diag["EMB"] = "RUN"

                            best_user = None
                            best_template = None
                            max_sim = -1.0

                            for u, t_list in db.items():
                                for t in t_list:
                                    d = embedder.compute_distance(emb, t)
                                    sim = 1.0 - d
                                    if sim > max_sim:
                                        max_sim = sim
                                        best_user = u
                                        best_template = t

                            t0 = time.time()
                            fiqa_weight = 1.0 if quality.passed else 0.4
                            decision = fusion.evaluate_fused(
                                passive_score=passive_score,
                                active_passed=True,
                                probe_emb=emb,
                                template_emb=best_template,
                                raw_cosine_sim=max_sim,
                                best_user=best_user,
                                fiqa_score=fiqa_weight
                            )
                            latencies["FUS"] = int((time.time() - t0) * 1000)
                            status_diag["FUS"] = f"RUN({int(decision.confidence_score*100)}%)"

                            if decision.success:
                                authenticated = True
                                auth_user = decision.matched_user
                                auth_conf = decision.confidence_score
                            else:
                                box_color = (0, 0, 255)
                                detail_text = f"Tu choi: {decision.reason}"

            # In Diagnostic Log len Terminal moi 0.15s (tranh nghen terminal)
            now = time.time()
            if now - last_log_time >= 0.15:
                fps = 1.0 / max(1e-4, now - t_frame_start)
                log_line = (
                    f"[F:{frame_idx:04d}|{fps:4.1f}FPS] "
                    f"DET:{status_diag['DET']}({latencies.get('DET',0)}ms) | "
                    f"FIQA:{status_diag['FIQA']}({latencies.get('FIQA',0)}ms) | "
                    f"FFT:{status_diag['FFT']} | "
                    f"LIVE:{status_diag['LIVE']} | "
                    f"PAD:{status_diag['PAD']} | "
                    f"EMB:{status_diag['EMB']}({latencies.get('EMB',0)}ms) | "
                    f"FUS:{status_diag['FUS']}"
                )
                print(log_line, end="\r")
                last_log_time = now

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

            cv2.imshow("FaceID Multi-Modal Pipeline (HM 4+5+6)", display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('r'):
                active_success = False
                authenticated = False
                current_action = challenge.reset_challenge()
                print(f"\n[*] Reset thu thach moi: {current_action.value}")

    except Exception as ex:
        print("\n[-] Xay ra loi:")
        traceback.print_exc()
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("\n[*] Da giai phong webcam va dong he thong.")

if __name__ == "__main__":
    main()
