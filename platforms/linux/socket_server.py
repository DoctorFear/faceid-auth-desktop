import os
import sys
import json
import time
import socket
import logging
import threading
import hashlib
from typing import Optional, Tuple

from core.security import RateLimiter, LockoutStatus
from core.fhs import validate_username, get_fhs_path
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


def verify_file_sha256(filepath: str, expected_hash: str) -> bool:
    """Xac minh tinh toan ven (Tamper Protection) cua file model ONNX qua SHA256."""
    if not os.path.exists(filepath):
        logger.error(f"[!] Khong tim thay file model: {filepath}")
        return False
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    actual_hash = sha256.hexdigest().lower()
    return actual_hash == expected_hash.lower()


def get_peer_credentials(sock: socket.socket) -> Tuple[int, int, int]:
    """
    Lay thong tin danh tinh cua tien trinh client qua SO_PEERCRED tren Linux:
    Tra ve tuple: (pid, uid, gid).
    Tren Windows (moi truong dev), tra ve (os.getpid(), 0, 0) de kiem thu.
    """
    if sys.platform != "win32" and hasattr(socket, "SO_PEERCRED"):
        import struct
        # struct ucred { pid_t pid; uid_t uid; gid_t gid; }; (3 ints: '3i')
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", creds)
        return pid, uid, gid
    return os.getpid(), 0, 0


class FaceIDDaemon:
    """
    FaceID Daemon Bao Mat Chay Ngam (Dual-Platform: Ubuntu 24.04 LTS / Windows Dev):
    1. Khoi dong thong minh: In banner thong tin he dieu hanh, socket/port dang hoat dong.
    2. Tamper Protection: Kiem tra checksum SHA-256 cua ca 3 models ONNX khi khoi dong.
    3. SO_PEERCRED: Kiem tra danh tinh process client goi den (chi root hoac cung user).
    4. Anti-Path-Traversal: Validate username regex theo chuan POSIX.
    5. Contextual Policy: Doc chinh sach rieng theo tung service PAM tu config/policy.json.
    6. Information Hiding: Client chi nhan FAILED, khong lo chi tiet bao mat ra ngoai.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        socket_path: Optional[str] = None,
        max_failures: Optional[int] = None,
        lockout_duration_sec: Optional[float] = None,
        verify_timeout_sec: Optional[float] = None
    ):
        self.os_type = "Windows (Dev Mode)" if sys.platform == "win32" else "Linux/Ubuntu 24.04 (Production Mode)"
        
        # Doc config tong
        cfg_file = config_path or get_fhs_path("config.json", category="config")
        self.config = {}
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                logger.warning(f"[-] Khong the doc file config {cfg_file}: {e}")

        sys_cfg = self.config.get("system", {})
        sec_cfg = self.config.get("security", {})
        cam_cfg = self.config.get("camera", {})

        self.enabled = sys_cfg.get("enabled", True)
        self.mode = sys_cfg.get("mode", "standalone_test")
        self.debug = sys_cfg.get("debug", True)
        self.socket_path = socket_path or sys_cfg.get("socket_path", DEFAULT_SOCKET_PATH)
        self.windows_port = sys_cfg.get("windows_port", 9876)
        self.verify_timeout_sec = verify_timeout_sec if verify_timeout_sec is not None else sys_cfg.get("verify_timeout_sec", 6.0)

        # Doc Contextual Policy
        self.policy = self._load_policy()

        # In Banner Khoi Dong
        self._print_startup_banner()

        # Kiem tra Tamper Protection SHA256
        if sec_cfg.get("verify_model_sha256", True):
            self._verify_models_integrity(sec_cfg.get("model_hashes", {}))

        # Khoi tao Rate Limiter
        rl_cfg = sec_cfg.get("rate_limiting", {})
        effective_max_failures = max_failures if max_failures is not None else rl_cfg.get("max_consecutive_failures", 3)
        effective_lockout = lockout_duration_sec if lockout_duration_sec is not None else rl_cfg.get("lockout_duration_sec", 45.0)

        self.rate_limiter = RateLimiter(
            max_failures=effective_max_failures,
            lockout_duration_sec=effective_lockout,
            max_requests_per_window=rl_cfg.get("flood_max_requests_per_sec", 5)
        )

        # Khoi tao Camera Manager
        self.camera_mgr = LinuxCameraManager(
            device_index=cam_cfg.get("device_index", 0),
            width=cam_cfg.get("width", 640),
            height=cam_cfg.get("height", 480),
            open_timeout_sec=cam_cfg.get("open_timeout_sec", 1.0)
        )

        # Nap san cac Engine AI vao RAM
        logger.info("[*] Dang nap san cac Model AI vao bo nho RAM...")
        t0 = time.time()
        self.detector = YuNetDetector()
        self.fiqa = FaceQualityAssessor()
        self.anti_spoof = MiniFASNetDetector(real_threshold=0.60)
        self.embedder = ArcFaceEmbedder()
        self.fusion = MultiModalFusionEngine(sim_threshold=0.62, decision_threshold=0.75)
        logger.info(f"[+] Da nap thanh cong toan bo AI Models vao RAM trong {(time.time() - t0):.2f}s!")

        self.running = False
        self._server_sock: Optional[socket.socket] = None

    def _load_policy(self) -> dict:
        """Nap file policy theo ngu canh dich vu (sudo, screensaver, gdm...)."""
        policy_file = get_fhs_path("policy.json", category="config")
        if os.path.exists(policy_file):
            try:
                with open(policy_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("contextual_policies", {})
            except Exception as e:
                logger.warning(f"[-] Khong the doc policy tu {policy_file}: {e}")
        return {}

    def _print_startup_banner(self):
        """In log khoi dong chi tiet de nguoi dung biet he thong dang hoat dong o moi truong nao."""
        listen_target = f"Port TCP {self.windows_port} (Local Loopback)" if sys.platform == "win32" else f"Unix Socket '{self.socket_path}'"
        banner = f"""
================================================================================
  FACEID HYBRID DAEMON - PHIEN BAN 2.1.0 (SECURE EDITION)
  He dieu hanh phat hien: {self.os_type}
  Trang thai hoat dong   : {'[BAT / ENABLED]' if self.enabled else '[TAT / DISABLED (Bypass)]'}
  Che do van hanh        : {self.mode.upper()}
  Diem lang nghe IPC     : {listen_target}
================================================================================
"""
        print(banner)
        logger.info(f"[*] Khoi dong FaceID Daemon tren {self.os_type} (Mode: {self.mode})")

    def _verify_models_integrity(self, expected_hashes: dict):
        """Xac minh tinh toan ven cryptographic hash SHA-256 cua toan bo model ONNX."""
        models_map = {
            "detector": "face_detection_yunet.onnx",
            "anti_spoof": "minifasnet_v2.onnx",
            "embedder": "arcface.onnx"
        }
        for name, rel_filename in models_map.items():
            full_path = get_fhs_path(rel_filename, category="models")
            expected = expected_hashes.get(name)
            if expected:
                if verify_file_sha256(full_path, expected):
                    logger.info(f"[+] SHA-256 Integrity Verified OK: {name} ({os.path.basename(full_path)})")
                else:
                    logger.error(f"[FATAL] Model {name} bi sai lech SHA-256 checksum! Co the file da bi sua doi.")
                    raise RuntimeError(f"Tamper Detection: Model {name} hash mismatch!")

    def authenticate_user(self, user: str, service: str = "sudo", peer_pid: int = 0, peer_uid: int = 0, client_conn: Optional[socket.socket] = None) -> dict:
        """Thuc hien xac thuc 1:1 voi Policy theo tung dich vu PAM."""
        # 1. Kiem tra cong tac tong
        if not self.enabled:
            logger.info(f"[*] FaceID dang o trang thai DISABLED -> Chuyen thang sang Password.")
            return {"status": "BYPASS", "fallback": "PASSWORD"}

        # 2. Validate Username Regex chong Path Traversal
        if not validate_username(user):
            logger.warning(f"[AUDIT] Phat hien username KHONG HOP LE tu PID {peer_pid}: '{user}'! Tu choi ngay.")
            return {"status": "FAILED", "fallback": "PASSWORD"}

        # 3. Kiem tra Lockout state
        lock_status = self.rate_limiter.get_lockout_status(user)
        if lock_status.is_locked:
            logger.warning(f"[AUDIT] Tu choi do Lockout: User '{user}' con {lock_status.remaining_seconds}s (PID: {peer_pid}).")
            return {"status": "LOCKED_OUT", "remaining_seconds": lock_status.remaining_seconds, "fallback": "PASSWORD"}

        # 4. Lay Contextual Policy cho service nay
        svc_policy = self.policy.get(service, self.policy.get("default", {}))
        decision_thresh = svc_policy.get("decision_threshold", 0.70)
        sim_thresh = svc_policy.get("sim_threshold", 0.62)
        require_active = svc_policy.get("require_active_liveness", True)
        timeout_sec = svc_policy.get("active_timeout_sec", self.verify_timeout_sec)

        logger.info(f"[AUDIT] Yeu cau xac thuc user='{user}' (Service='{service}', PID={peer_pid}, Thresh={decision_thresh})")

        # 5. Kiem tra template
        templates = load_user_templates(user)
        if templates is None or len(templates) == 0:
            logger.warning(f"[AUDIT] User '{user}' chua dang ky FaceID (Chua enroll).")
            return {"status": "NOT_ENROLLED", "fallback": "PASSWORD"}

        def send_progress(msg: str):
            """Gui huong dan truc tiep ve terminal cua client."""
            if client_conn:
                try:
                    payload = json.dumps({"event": "progress", "message": msg}) + "\n"
                    client_conn.sendall(payload.encode("utf-8"))
                except Exception:
                    pass

        # 6. Mo camera an toan
        try:
            with self.camera_mgr.acquire() as cap:
                logger.info(f"[*] Camera BAT thanh cong. Tien hanh quet khuon mat...")
                send_progress("Camera da bat. Vui long nhin thang vao camera...")
                challenge = ChallengeResponseDetector(timeout_sec=timeout_sec, preload_hands=False)
                if require_active:
                    chosen_action = challenge.reset_challenge()
                    send_progress(f"[*] YEU CAU LIVENESS: {chosen_action.value}")
                t_start = time.time()

                best_decision = None
                active_passed = not require_active
                last_hint_sent = ""

                while time.time() - t_start < timeout_sec:
                    ret, frame = cap.read()
                    if not ret or frame is None or frame.size == 0:
                        time.sleep(0.02)
                        continue

                    # Pipeline AI
                    detections = self.detector.detect(frame)
                    if len(detections) != 1:
                        continue

                    box, landmarks, det_conf = detections[0]
                    quality = self.fiqa.evaluate_quality(frame, box, landmarks)
                    pose = (quality.yaw, quality.pitch, quality.roll)

                    if not active_passed:
                        p, hint, _ = challenge.check_action(frame, pose, landmarks, box)
                        if hint and hint != last_hint_sent:
                            logger.info(f"[*] Thu thach liveness: {hint}")
                            send_progress(f"[*] YEU CAU LIVENESS: {hint}")
                            last_hint_sent = hint
                        if p:
                            active_passed = True
                            logger.info("[+] Thu thach liveness: HOAN THANH!")
                            send_progress("[+] Thu thach Liveness DAT! Dang so khop danh tinh...")

                    if active_passed:
                        if not quality.passed:
                            continue
                        is_real, passive_score, _ = self.anti_spoof.predict_smoothed(frame, box)
                        emb = self.embedder.extract(frame, box, landmarks=landmarks)
                        
                        # So khop voi 5 Pose Prototypes (Top-2 Regularized)
                        sim_pairs = []
                        for t in templates:
                            d = self.embedder.compute_distance(emb, t)
                            sim_pairs.append((float(1.0 - d), t))
                        sim_pairs.sort(key=lambda x: x[0], reverse=True)
                        best_sim, best_t = sim_pairs[0]

                        if len(sim_pairs) >= 2:
                            effective_sim = 0.70 * best_sim + 0.30 * sim_pairs[1][0]
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

                        if decision.confidence_score >= decision_thresh:
                            best_decision = decision
                            break

                # Ket qua xac thuc
                if best_decision and best_decision.confidence_score >= decision_thresh:
                    self.rate_limiter.record_success(user)
                    logger.info(f"[AUDIT] AUTH_SUCCESS user='{user}' svc='{service}' conf={best_decision.confidence_score:.3f}")
                    return {
                        "status": "SUCCESS",
                        "user": user,
                        "confidence": float(best_decision.confidence_score)
                    }
                else:
                    new_status = self.rate_limiter.record_failure(user)
                    logger.warning(f"[AUDIT] AUTH_FAILED user='{user}' svc='{service}' (Con lai {3 - new_status.consecutive_failures} lan)")
                    # Client chi nhan FAILED, khong ro ri chi tiet ky thuat ra ben ngoai
                    return {
                        "status": "FAILED",
                        "fallback": "PASSWORD"
                    }

        except CameraBusyError as e:
            logger.warning(f"[AUDIT] CAMERA_BUSY tu PID {peer_pid}: {e}. Fallback password ngay.")
            return {"status": "CAMERA_BUSY", "fallback": "PASSWORD"}
        except Exception as e:
            logger.error(f"[AUDIT] System Error: {e}")
            return {"status": "FAILED", "fallback": "PASSWORD"}

    def handle_client(self, conn: socket.socket):
        """Xu ly mot phien ket noi IPC tu PAM module kem kiem tra SO_PEERCRED."""
        peer_pid, peer_uid, peer_gid = 0, 0, 0
        try:
            conn.settimeout(10.0)
            
            # Kiem tra danh tinh process client qua SO_PEERCRED tren Linux
            peer_pid, peer_uid, peer_gid = get_peer_credentials(conn)
            
            data = conn.recv(4096).decode("utf-8").strip()
            if not data:
                return

            req = json.loads(data)
            action = req.get("action", "auth")
            user = req.get("user", "").strip()
            service = req.get("service", "sudo").strip()

            # Kiem tra SO_PEERCRED:
            # Tren Linux, tien trinh gui AUTH chi duoc phep la root (UID 0 tu PAM)
            # hoac chinh la process cua user do dang chay.
            if sys.platform != "win32" and peer_uid != 0:
                # Kiem tra xem peer_uid co khop voi username yeu cau khong
                import pwd
                try:
                    caller_name = pwd.getpwuid(peer_uid).pw_name
                    if caller_name != user:
                        logger.warning(f"[SECURITY] Process PID {peer_pid} (UID {peer_uid}:{caller_name}) co tinh goi AUTH cho user '{user}'! BI CHAN.")
                        conn.sendall(json.dumps({"status": "PERMISSION_DENIED", "fallback": "PASSWORD"}).encode("utf-8") + b"\n")
                        return
                except Exception:
                    pass

            # Kiem tra DoS Flood Attack
            if self.rate_limiter.check_flood_attack():
                resp = {"status": "RATE_LIMITED", "fallback": "PASSWORD"}
            elif action == "auth" and user:
                resp = self.authenticate_user(user, service=service, peer_pid=peer_pid, peer_uid=peer_uid, client_conn=conn)
            elif action == "status":
                resp = {"status": "OK", "os": self.os_type, "enabled": self.enabled, "mode": self.mode}
            else:
                resp = {"status": "FAILED", "fallback": "PASSWORD"}

            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception as e:
            logger.error(f"Loi xu ly client IPC: {e}")
            try:
                conn.sendall(json.dumps({"status": "FAILED", "fallback": "PASSWORD"}).encode("utf-8") + b"\n")
            except Exception:
                pass
        finally:
            conn.close()

    def start(self):
        """Khoi chay Server Socket."""
        sock_dir = os.path.dirname(os.path.abspath(self.socket_path))
        os.makedirs(sock_dir, exist_ok=True)

        if os.path.exists(self.socket_path) and sys.platform != "win32":
            try:
                os.remove(self.socket_path)
            except OSError:
                pass

        if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
            self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server_sock.bind(self.socket_path)
            os.chmod(self.socket_path, 0o660)
            logger.info(f"[+] Lang nghe Unix Socket tai: {self.socket_path}")
        else:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(("127.0.0.1", self.windows_port))
            logger.info(f"[+] Lang nghe TCP Socket tai: 127.0.0.1:{self.windows_port} (Windows Dev Mode)")

        self._server_sock.listen(16)
        self.running = True

        while self.running:
            try:
                conn, _ = self._server_sock.accept()
                client_thread = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                client_thread.start()
            except Exception as e:
                if self.running:
                    logger.error(f"Loi chap nhan ket noi: {e}")

    def stop(self):
        """Dung daemon va don dep socket."""
        self.running = False
        if self._server_sock:
            self._server_sock.close()
        if os.path.exists(self.socket_path) and sys.platform != "win32":
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
        logger.info("[*] FaceID Daemon da dung an toan.")
