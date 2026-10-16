import os
import sys
import json
import time
import socket
import logging
import threading
from typing import Optional

from core.security import RateLimiter, LockoutStatus
from platforms.linux.camera import LinuxCameraManager, CameraBusyError, CameraDeviceError
from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.liveness import ChallengeResponseDetector
from core.fusion import MultiModalFusionEngine
from scripts.enroll import load_user_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [FaceID-Daemon] %(message)s")
logger = logging.getLogger("faceid.daemon")

DEFAULT_SOCKET_PATH = "/run/faceid/faceid.sock" if sys.platform != "win32" else "data/faceid.sock"


class FaceIDDaemon:
    """
    Tien trinh Daemon chay ngam xu ly xac thuc sinh trac hoc FaceID tren Linux:
    - Lang nghe yeu cau xac thuc tu PAM Module qua Unix Domain Socket.
    - Tich hop RateLimiter: Khoa tai khoan khi that bai lien tiep (Anti-Brute-Force & DoS Flood Protection).
    - Xu ly tranh chap thiet bi Camera (Device Contention): Tra ve CAMERA_BUSY ngay lap tuc de PAM chuyen sang password.
    - Tuan thu Nghi dinh 13/2023/ND-CP: Khong luu anh (Zero-Image Retention), chi mo camera khi co request tu PAM.
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        max_failures: int = 3,
        lockout_duration_sec: float = 45.0,
        verify_timeout_sec: float = 6.0
    ):
        self.socket_path = socket_path
        self.verify_timeout_sec = verify_timeout_sec
        self.rate_limiter = RateLimiter(max_failures=max_failures, lockout_duration_sec=lockout_duration_sec)
        self.camera_mgr = LinuxCameraManager()

        logger.info("[*] Dang khoi tao cac Engine AI (YuNet, MiniFASNet, ArcFace, Fusion)...")
        self.detector = YuNetDetector()
        self.fiqa = FaceQualityAssessor()
        self.anti_spoof = MiniFASNetDetector(real_threshold=0.60)
        self.embedder = ArcFaceEmbedder()
        self.fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.75)
        self.running = False
        self._server_sock: Optional[socket.socket] = None

    def authenticate_user(self, user: str, service: str = "sudo") -> dict:
        """
        Thuc hien chu trinh xac thuc FaceID hoan chinh cho 1 user:
        1. Kiem tra Lockout state.
        2. Kiem tra va tai template vector da enroll.
        3. Mo camera an toan (voi co che bat/tat den LED va chong xung dot).
        4. Chay pipeline AI (Detect -> FIQA -> PAD -> Liveness -> ArcFace -> S-Norm Fusion).
        5. Cap nhat RateLimiter va tra ve ket qua cho PAM.
        """
        # 1. Kiem tra Lockout
        lock_status = self.rate_limiter.get_lockout_status(user)
        if lock_status.is_locked:
            logger.warning(f"[!] Tu choi xac thuc: {lock_status.reason}")
            return {
                "status": "LOCKED_OUT",
                "remaining_seconds": lock_status.remaining_seconds,
                "message": lock_status.reason,
                "fallback": "PASSWORD"
            }

        # 2. Kiem tra template
        templates = load_user_templates(user)
        if not templates:
            logger.warning(f"[!] User '{user}' chua dang ky khuon mat (Chua enroll).")
            return {
                "status": "NOT_ENROLLED",
                "message": f"Nguoi dung '{user}' chua dang ky FaceID.",
                "fallback": "PASSWORD"
            }

        # 3. Mo camera an toan
        try:
            with self.camera_mgr.acquire() as cap:
                logger.info(f"[*] Bat dau xac thuc FaceID cho user '{user}' (Service: {service}). Den LED Camera BAT.")
                challenge = ChallengeResponseDetector(timeout_sec=self.verify_timeout_sec, preload_hands=False)
                t_start = time.time()

                best_decision = None
                active_passed = False

                while time.time() - t_start < self.verify_timeout_sec:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        time.sleep(0.02)
                        continue

                    # Pipeline AI
                    detections = self.detector.detect(frame)
                    if len(detections) != 1:
                        continue  # Yeu cau dung 1 khuon mat

                    box, landmarks, det_conf = detections[0]
                    quality = self.fiqa.evaluate_quality(frame, box, landmarks)
                    if not quality.passed:
                        continue

                    is_real, passive_score, _ = self.anti_spoof.predict_smoothed(frame, box)
                    pose = (quality.yaw, quality.pitch, quality.roll)

                    if not active_passed:
                        p, hint, _ = challenge.check_action(frame, pose, landmarks, box)
                        if p:
                            active_passed = True

                    if active_passed:
                        emb = self.embedder.extract(frame, box, landmarks=landmarks)
                        # So khop voi templates (Top-2 Regularized Fusion)
                        sim_pairs = []
                        for t in templates:
                            d = self.embedder.compute_distance(emb, t)
                            sim_pairs.append((float(1.0 - d), t))
                        sim_pairs.sort(key=lambda x: x[0], reverse=True)
                        best_sim, best_t = sim_pairs[0]

                        if len(sim_pairs) >= 2:
                            second_sim = sim_pairs[1][0]
                            effective_sim = 0.70 * best_sim + 0.30 * second_sim
                        else:
                            effective_sim = best_sim

                        effective_passive = passive_score if quality.is_live_fft else (passive_score * 0.82)
                        fiqa_weight = (1.0 if quality.passed else 0.4) * (1.0 if quality.is_live_fft else 0.85)

                        decision = self.fusion.evaluate_fused(
                            passive_score=effective_passive,
                            active_passed=True,
                            probe_emb=emb,
                            template_emb=best_t,
                            raw_cosine_sim=effective_sim,
                            best_user=user,
                            fiqa_score=fiqa_weight
                        )

                        if decision.success:
                            best_decision = decision
                            break

                # Ket thuc phien camera: Context manager tu dong dong camera va tat den LED
                if best_decision and best_decision.success:
                    self.rate_limiter.record_success(user)
                    logger.info(f"[+] Xac thuc THANH CONG user '{user}' (Confidence: {best_decision.confidence_score:.3f}). Den LED TAT.")
                    return {
                        "status": "SUCCESS",
                        "user": user,
                        "confidence": float(best_decision.confidence_score),
                        "message": "Xac thuc FaceID thanh cong."
                    }
                else:
                    new_status = self.rate_limiter.record_failure(user)
                    logger.warning(f"[-] Xac thuc THAT BAI user '{user}'. {new_status.reason}. Den LED TAT.")
                    return {
                        "status": "FAILED",
                        "user": user,
                        "consecutive_failures": new_status.consecutive_failures,
                        "message": new_status.reason,
                        "fallback": "PASSWORD"
                    }

        except CameraBusyError as e:
            logger.warning(f"[!] Xung dot thiet bi Camera: {e}. Fallback ngay ve Password.")
            return {
                "status": "CAMERA_BUSY",
                "message": str(e),
                "fallback": "PASSWORD"
            }
        except Exception as e:
            logger.error(f"[!] Loi he thong camera: {e}")
            return {
                "status": "ERROR",
                "message": str(e),
                "fallback": "PASSWORD"
            }

    def handle_client(self, conn: socket.socket):
        """Xu ly mot phien ket noi IPC tu PAM module."""
        try:
            conn.settimeout(10.0)
            data = conn.recv(4096).decode("utf-8").strip()
            if not data:
                return

            req = json.loads(data)
            action = req.get("action", "auth")
            user = req.get("user", "")
            service = req.get("service", "sudo")

            # Kiem tra DoS Flood Attack
            if self.rate_limiter.check_flood_attack():
                resp = {"status": "RATE_LIMITED", "message": "Qua nhieu yeu cau trong thoi gian ngan.", "fallback": "PASSWORD"}
            elif action == "auth" and user:
                resp = self.authenticate_user(user, service=service)
            elif action == "status":
                lock_info = self.rate_limiter.get_lockout_status(user)
                resp = {"status": "OK", "locked": lock_info.is_locked, "remaining": lock_info.remaining_seconds}
            else:
                resp = {"status": "INVALID_REQUEST", "fallback": "PASSWORD"}

            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception as e:
            logger.error(f"Loi xu ly client IPC: {e}")
            try:
                err_resp = {"status": "ERROR", "message": str(e), "fallback": "PASSWORD"}
                conn.sendall((json.dumps(err_resp) + "\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def start(self):
        """Khoi chay Unix Domain Socket Server."""
        # Tao thu muc chua socket neu chua co
        sock_dir = os.path.dirname(os.path.abspath(self.socket_path))
        os.makedirs(sock_dir, exist_ok=True)

        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except OSError:
                pass

        # Tren Windows su dung AF_INET loopback de test IPC neu AF_UNIX chua ho tro
        if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
            self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_sock.bind(self.socket_path)
            # Phan quyen 0660 cho socket de user trong group faceid co the ket noi
            os.chmod(self.socket_path, 0o660)
        else:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.bind(("127.0.0.1", 9876))

        self._server_sock.listen(16)
        self.running = True
        logger.info(f"[+] FaceID Daemon da san sang lang nghe tai: {self.socket_path}")

        while self.running:
            try:
                conn, _ = self._server_sock.accept()
                client_thread = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                client_thread.start()
            except Exception as e:
                if self.running:
                    logger.error(f"Loi chap nhan ket noi: {e}")

    def stop(self):
        """Dung daemon va don dep socket file."""
        self.running = False
        if self._server_sock:
            self._server_sock.close()
        if os.path.exists(self.socket_path) and sys.platform != "win32":
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
        logger.info("[*] FaceID Daemon da dung.")
