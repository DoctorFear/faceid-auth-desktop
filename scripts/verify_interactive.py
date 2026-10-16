import argparse
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


def consolidate_templates_to_k_prototypes(vectors: np.ndarray, k: int = 5) -> np.ndarray:
    """Gom cum va nen tap embedding ve dung k=5 vector dai dien (Spherical K-Means) de triet tieu FAR inflation."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    vecs = vectors / norms
    if len(vecs) <= k:
        return vecs.astype(np.float32)

    rng = np.random.default_rng(42)
    centers = [vecs[rng.choice(len(vecs))]]
    for _ in range(1, k):
        sims = np.max([np.dot(vecs, c) for c in centers], axis=0)
        dists = np.maximum(0.0, 1.0 - sims)
        probs = dists / (np.sum(dists) + 1e-9)
        centers.append(vecs[rng.choice(len(vecs), p=probs)])
    centers = np.array(centers)

    for _ in range(20):
        assignments = np.argmax(np.dot(vecs, centers.T), axis=1)
        new_centers = []
        for j in range(k):
            members = vecs[assignments == j]
            if len(members) > 0:
                c = np.mean(members, axis=0)
                new_centers.append(c / (np.linalg.norm(c) + 1e-9))
            else:
                new_centers.append(centers[j])
        new_centers = np.array(new_centers, dtype=np.float32)
        if np.allclose(centers, new_centers, atol=1e-4):
            break
        centers = new_centers
    return centers.astype(np.float32)


def load_user_template(target_user: str) -> np.ndarray | None:
    """Xac minh 1:1 - Chi nạp dung template cua user duoc chi dinh de giu FAR thap.
    Tu dong nén ve toi da 5 Pose Prototypes neu file chua qua nhieu vector."""
    user_file = os.path.join(EMB_DIR, f"{target_user}.npy")
    if not os.path.exists(user_file):
        return None
    try:
        arr = np.load(user_file, allow_pickle=False).astype(np.float32)
        if arr.ndim == 2:
            if len(arr) > 5:
                print(f"[*] Template user '{target_user}' co {len(arr)} vectors. Tu dong nén ve 5 Pose Prototypes...")
                arr = consolidate_templates_to_k_prototypes(arr, k=5)
            norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
            return (arr / norms).astype(np.float32)
        return arr
    except Exception as e:
        print(f"[-] Loi khi doc template user '{target_user}': {e}")
        return None


def check_box_continuity(prev_box: tuple, curr_box: tuple, max_disp_ratio: float = 0.55) -> bool:
    """Kiem tra tinh lien tuc cua toa do khuon mat giua 2 frame de ngan chan trao mat (Face Swap / Injection)."""
    pt, pr, pb, pl = prev_box
    ct, cr, cb, cl = curr_box
    prev_cx, prev_cy = (pl + pr) / 2.0, (pt + pb) / 2.0
    curr_cx, curr_cy = (cl + cr) / 2.0, (ct + cb) / 2.0
    diag = np.sqrt((pr - pl) ** 2 + (pb - pt) ** 2) + 1e-6
    disp = np.sqrt((curr_cx - prev_cx) ** 2 + (curr_cy - prev_cy) ** 2)
    return float(disp / diag) < max_disp_ratio


def main():
    parser = argparse.ArgumentParser(description="Kiem thu FaceID Pipeline xac thuc 1:1 (PAM Model)")
    parser.add_argument("--user", type=str, default="", help="Username can xac minh 1:1")
    args = parser.parse_args()

    target_user = args.user.strip()
    if not target_user:
        if os.path.exists(EMB_DIR):
            users = [f[:-4] for f in os.listdir(EMB_DIR) if f.endswith(".npy")]
            if users:
                target_user = sorted(users)[0]
                print(f"[*] Khong truyen --user, tu dong chon user: '{target_user}'.")

    if not target_user:
        print("[-] Loi: Chua co du lieu nguoi dung nao trong data/embeddings. Hay chay enroll.py truoc.")
        return

    user_templates = load_user_template(target_user)
    if user_templates is None or len(user_templates) == 0:
        print(f"[-] Khong tim thay template hop le cho tai khoan: '{target_user}'")
        return

    print(f"[*] Che do xac minh 1:1 cho user: '{target_user}' ({len(user_templates)} vectors)")

    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[-] LOI: Khong the mo webcam.")
            return

        print("[*] Khoi tao module AI (preload MediaPipe & warm-up)...")
        detector = YuNetDetector()
        fiqa = FaceQualityAssessor()
        challenge = ChallengeResponseDetector(timeout_sec=16.0, preload_hands=True)
        anti_spoof = MiniFASNetDetector(real_threshold=0.60)
        embedder = ArcFaceEmbedder()
        fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.65)

        ACTIVE_WINDOW_SEC = 1.2       # Thoi gian hop le toi da (giay) sau khi pass challenge de hoan tat match template
        MAX_CONSECUTIVE_REJECTS = 3   # So frame mismatch lien tiep toi da truoc khi huy phien liveness

        current_action = challenge.reset_challenge()
        active_success = False
        active_success_time = 0.0
        authenticated = False
        auth_conf = 0.0
        last_face_box = None
        consecutive_rejects = 0

        cv2.namedWindow("FaceID 1:1 Verification (PAM Model)", cv2.WINDOW_AUTOSIZE)
        print("\n>>> BANG THEO DOI HOAT DONG MODULE (1:1 AUTH) <<<")
        print("Trang thai: [RUN]=Dang chay | [WAIT]=Cho dieu kien | [PASS]=Dat chuan | [FAIL]=Khong dat\n")

        frame_idx = 0
        last_log_time = time.time()

        while True:
            t_frame_start = time.time()
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                continue

            frame_idx += 1
            h, w = frame.shape[:2]

            status_diag = {
                "DET": "SKIP", "FIQA": "SKIP", "FFT": "SKIP",
                "LIVE": "SKIP", "PAD": "SKIP", "EMB": "WAIT", "FUS": "WAIT"
            }
            latencies = {}

            # Kiem tra het han cua cua so Active Liveness (Session Validity Window)
            if active_success and not authenticated:
                if time.time() - active_success_time > ACTIVE_WINDOW_SEC:
                    active_success = False
                    active_success_time = 0.0
                    consecutive_rejects = 0
                    last_face_box = None
                    current_action = challenge.reset_challenge()
                    detail_text = "Het thoi gian hop le (Timeout 1.2s)! Reset thu thach."

            # 1. DETECTOR
            t0 = time.time()
            detections = detector.detect(frame)
            latencies["DET"] = int((time.time() - t0) * 1000)
            status_diag["DET"] = f"RUN({len(detections)})"

            if authenticated:
                status_text = f"XAC THUC THANH CONG: {target_user}"
            elif active_success:
                remaining_window = max(0.0, ACTIVE_WINDOW_SEC - (time.time() - active_success_time))
                status_text = f"[{target_user}] DANG XAC THUC ({remaining_window:.1f}s)..."
            else:
                status_text = f"[{target_user}] THU THACH: {current_action.value}"

            detail_text = ""
            box_color = (0, 200, 255)
            box_mirrored = None
            fingertips_draw = []

            if authenticated:
                box_color = (0, 255, 0)
                status_text = f"XAC THUC THANH CONG: {target_user}"
                detail_text = f"Do tin cay tong hop: {auth_conf*100:.1f}% | Nhan 'r' de thu lai"
                status_diag["EMB"] = "DONE"
                status_diag["FUS"] = "DONE"

            elif len(detections) == 0:
                # KHI MAT DAU KHUON MAT:
                if active_success:
                    # Neu truoc do da pass active liveness ma mat bien mat -> Huy ngay (Anti-Hijacking / Face Swap)
                    active_success = False
                    active_success_time = 0.0
                    consecutive_rejects = 0
                    last_face_box = None
                    current_action = challenge.reset_challenge()
                    detail_text = "Mat dau khuon mat! Da huy phien xac thuc de bao mat."
                else:
                    detail_text = "Dua khuon mat vao khung hinh..."
                    last_face_box = None

            elif len(detections) > 1:
                if active_success:
                    active_success = False
                    active_success_time = 0.0
                    consecutive_rejects = 0
                    last_face_box = None
                    current_action = challenge.reset_challenge()
                    detail_text = "Phat hien nhieu hon 1 mat! Da huy phien xac thuc."
                else:
                    detail_text = "Canh bao: Phat hien nhieu hon 1 khuon mat"
                    last_face_box = None
                status_diag["FIQA"] = "FAIL(MultiFace)"

            else:
                box, landmarks, _ = detections[0]
                top, right, bottom, left = box
                box_mirrored = (w - right, top, w - left, bottom)

                # Kiem tra tinh lien tuc khong gian (Spatial Continuity Tracking chong trao mat dot ngot)
                if last_face_box is not None and not check_box_continuity(last_face_box, box):
                    if active_success:
                        active_success = False
                        active_success_time = 0.0
                        consecutive_rejects = 0
                        current_action = challenge.reset_challenge()
                        detail_text = "Phat hien trao mat dot ngot (Face Swap)! Reset thu thach."
                last_face_box = box

                # 2. FIQA & FFT MOIRE
                t0 = time.time()
                quality = fiqa.evaluate_quality(frame, box, landmarks)
                latencies["FIQA"] = int((time.time() - t0) * 1000)
                status_diag["FIQA"] = "PASS" if quality.passed else "FAIL"
                status_diag["FFT"] = "PASS" if quality.is_live_fft else f"WARN({quality.moire_score:.2f})"
                pose = (quality.yaw, quality.pitch, quality.roll)

                # 3. PASSIVE PAD
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
                        active_success_time = time.time()
                        consecutive_rejects = 0
                        status_diag["LIVE"] = "SUCCESS"
                else:
                    remaining_window = max(0.0, ACTIVE_WINDOW_SEC - (time.time() - active_success_time))
                    status_diag["LIVE"] = f"PASS({remaining_window:.1f}s)"

                    # 5. ARCFACE EMBEDDER + ALIGNMENT + FUSION 1:1
                    if not authenticated:
                        if not quality.passed:
                            box_color = (0, 140, 255)
                            detail_text = f"FIQA: {quality.reason} (Con {remaining_window:.1f}s)"
                            status_diag["EMB"] = "WAIT(FIQA)"
                            status_diag["FUS"] = "WAIT(FIQA)"
                        else:
                            t0 = time.time()
                            emb = embedder.extract(frame, box, landmarks=landmarks)
                            latencies["EMB"] = int((time.time() - t0) * 1000)
                            status_diag["EMB"] = "RUN"

                            # So khop 1:1 voi tap vector cua target_user (Top-2 Regularized Fusion chong FAR Inflation)
                            sim_pairs = []
                            for t in user_templates:
                                d = embedder.compute_distance(emb, t)
                                sim = float(1.0 - d)
                                sim_pairs.append((sim, t))

                            sim_pairs.sort(key=lambda x: x[0], reverse=True)
                            best_sim, best_t = sim_pairs[0]

                            # Tinh diem tuong dong hieu dung (Regularized Multi-Template Score):
                            # Voi chinh chu: ca 2 vector tot nhat deu co do tuong dong cao -> diem on dinh
                            # Voi ke mao danh: best_sim co the do may rui mot vector cao, second_sim se tut sau -> bi ha diem ngay
                            if len(sim_pairs) >= 2:
                                second_sim = sim_pairs[1][0]
                                effective_sim = 0.70 * best_sim + 0.30 * second_sim
                            else:
                                effective_sim = best_sim

                            t0 = time.time()
                            # FFT Moire soft-indicator: neu phat hien van Moire man hinh, ap dung soft penalty
                            # ho tro MiniFASNet ha diem tin cay thay vi Hard-Reject cuc doan
                            effective_passive = passive_score if quality.is_live_fft else (passive_score * 0.82)
                            fiqa_weight = (1.0 if quality.passed else 0.4) * (1.0 if quality.is_live_fft else 0.85)

                            decision = fusion.evaluate_fused(
                                passive_score=effective_passive,
                                active_passed=True,
                                probe_emb=emb,
                                template_emb=best_t,
                                raw_cosine_sim=effective_sim,
                                best_user=target_user,
                                fiqa_score=fiqa_weight
                            )
                            latencies["FUS"] = int((time.time() - t0) * 1000)
                            status_diag["FUS"] = f"RUN({int(decision.confidence_score*100)}%)"

                            if decision.success:
                                authenticated = True
                                auth_conf = decision.confidence_score
                                consecutive_rejects = 0
                            else:
                                box_color = (0, 0, 255)
                                consecutive_rejects += 1
                                # Neu do tuong dong qua thap (< 0.40) hoac mismatch qua 3 frame -> Huy token liveness ngay
                                if consecutive_rejects >= MAX_CONSECUTIVE_REJECTS or effective_sim < 0.40:
                                    active_success = False
                                    active_success_time = 0.0
                                    consecutive_rejects = 0
                                    last_face_box = None
                                    current_action = challenge.reset_challenge()
                                    detail_text = f"Huy phien: Khong khop danh tinh ({decision.reason})!"
                                else:
                                    detail_text = f"Tu choi ({consecutive_rejects}/{MAX_CONSECUTIVE_REJECTS}): {decision.reason}"

            # Log chan doan len Terminal
            now = time.time()
            if now - last_log_time >= 0.15:
                fps = 1.0 / max(1e-4, now - t_frame_start)
                log_line = (
                    f"[{target_user}|{fps:4.1f}FPS] "
                    f"DET:{status_diag['DET']}({latencies.get('DET',0)}ms) | "
                    f"FIQA:{status_diag['FIQA']} | "
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
            cv2.putText(display, "Nhan 'r': Reset | 'q': Thoat", (15, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)

            cv2.imshow("FaceID 1:1 Verification (PAM Model)", display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('r'):
                active_success = False
                active_success_time = 0.0
                authenticated = False
                auth_conf = 0.0
                last_face_box = None
                consecutive_rejects = 0
                current_action = challenge.reset_challenge()
                print(f"\n[*] Reset thu thach moi: {current_action.value}")

    except Exception:
        print("\n[-] Xay ra loi:")
        traceback.print_exc()
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("\n[*] Da giai phong webcam va dung tien trinh.")


if __name__ == "__main__":
    main()