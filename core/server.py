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
from platforms.linux.camera import LinuxCameraManager, CameraBusyError, CameraDeviceError, VirtualCameraDetectedError
from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.liveness import ChallengeResponseDetector, ActionType
from core.fusion import MultiModalFusionEngine
from core.constants import (
    MIN_FACE_SIZE_PX,
    STRICT_FRONTAL_MAX_YAW,
    STRICT_FRONTAL_MAX_PITCH,
    FRONTAL_STREAK_REQUIRED,
    POSE_RETURN_MAX_DYAW,
    POSE_RETURN_MAX_DPITCH,
    PAD_REAL_THRESHOLD,
    MOIRE_PASSIVE_PENALTY,
    MOIRE_FIQA_PENALTY,
    LOW_FIQA_PENALTY,
    TOP1_TEMPLATE_WEIGHT,
    TOP2_TEMPLATE_WEIGHT,
    DEFAULT_SIM_THRESHOLD,
    DEFAULT_DECISION_THRESHOLD,
    DEFAULT_VERIFY_TIMEOUT_SEC,
    POST_LIVENESS_WINDOW_SEC,
    DEFAULT_MAX_FAILURES,
    DEFAULT_LOCKOUT_SEC,
)
from scripts.enroll import load_user_templates, load_all_enrolled_templates

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
    4. Anti-Path-Traversal: Validate username regex theo chuan POSIX / Windows.
    5. Contextual Policy: Doc chinh sach rieng theo tung service PAM / Windows tu config/policy.json.
    6. Information Hiding: Client chi nhan FAILED, khong lo chi tiet bao mat ra ngoai.
    7. Hot-Reload & Dynamic Control: Ho tro bat/tat (on/off) va dung daemon (stop) qua IPC tuc thi.
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
        self.cfg_file = config_path or get_fhs_path("config.json", category="config")
        self.config = {}
        if os.path.exists(self.cfg_file):
            try:
                with open(self.cfg_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = {}

        sys_cfg = self.config.get("system", {})
        sec_cfg = self.config.get("security", {})
        cam_cfg = self.config.get("camera", {})

        self.enabled = sys_cfg.get("enabled", True)
        self.mode = sys_cfg.get("mode", "standalone_test")
        self.debug = sys_cfg.get("debug", True)
        self.socket_path = socket_path or sys_cfg.get("socket_path", DEFAULT_SOCKET_PATH)
        self.windows_port = sys_cfg.get("windows_port", 9876)
        self.verify_timeout_sec = verify_timeout_sec if verify_timeout_sec is not None else sys_cfg.get("verify_timeout_sec", DEFAULT_VERIFY_TIMEOUT_SEC)
        self.pid_file = get_fhs_path("daemon.pid", category="data")

        # Doc Contextual Policy
        self.policy = self._load_policy()

        # In Banner Khoi Dong
        self._print_startup_banner()

        # Kiem tra Tamper Protection SHA256
        if sec_cfg.get("verify_model_sha256", True):
            self._verify_models_integrity(sec_cfg.get("model_hashes", {}))

        # Khoi tao Rate Limiter
        rl_cfg = sec_cfg.get("rate_limiting", {})
        effective_max_failures = max_failures if max_failures is not None else rl_cfg.get("max_consecutive_failures", DEFAULT_MAX_FAILURES)
        effective_lockout = lockout_duration_sec if lockout_duration_sec is not None else rl_cfg.get("lockout_duration_sec", DEFAULT_LOCKOUT_SEC)

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
        self.anti_spoof = MiniFASNetDetector(real_threshold=PAD_REAL_THRESHOLD)
        self.embedder = ArcFaceEmbedder()
        self.fusion = MultiModalFusionEngine(sim_threshold=DEFAULT_SIM_THRESHOLD, decision_threshold=DEFAULT_DECISION_THRESHOLD)
        logger.info(f"[+] Da nap thanh cong toan bo AI Models vao RAM trong {(time.time() - t0):.2f}s!")

        self.running = False
        self._auth_lock = threading.Lock()
        self._server_sock: Optional[socket.socket] = None

        if sys.platform != "win32":
            try:
                from platforms.linux.gdm_hook import sync_gdm_localization
                sync_gdm_localization(self.enabled)
            except Exception:
                pass

    def _reload_config(self):
        """Doc lai config.json va policy.json de cap nhat trang thai enabled/policy tuc thi (Hot-Reload)."""
        if os.path.exists(self.cfg_file):
            try:
                with open(self.cfg_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                sys_cfg = self.config.get("system", {})
                self.enabled = sys_cfg.get("enabled", True)
                self.mode = sys_cfg.get("mode", "standalone_test")

                # Hot-reload camera device_index
                cam_cfg = self.config.get("camera", {})
                new_dev_idx = cam_cfg.get("device_index", 0)
                if hasattr(self.camera_mgr, "device_index") and self.camera_mgr.device_index != new_dev_idx:
                    logger.info(f"[*] Hot-Reload: Cap nhat Camera Device Index: {self.camera_mgr.device_index} -> {new_dev_idx}")
                    self.camera_mgr.device_index = new_dev_idx

                # Hot-reload max_consecutive_failures (doc truc tiep tu root cua policy.json)
                policy_file = get_fhs_path("policy.json", category="config")
                if os.path.exists(policy_file):
                    try:
                        with open(policy_file, "r", encoding="utf-8") as pf:
                            full_pol = json.load(pf)
                        rl_data = full_pol.get("rate_limit", {})
                        new_max_fail = int(rl_data.get("max_consecutive_failures", DEFAULT_MAX_FAILURES))
                        new_lockout = float(rl_data.get("lockout_duration_sec", DEFAULT_LOCKOUT_SEC))
                        if hasattr(self, "rate_limiter"):
                            if self.rate_limiter.max_failures != new_max_fail:
                                self.rate_limiter.max_failures = new_max_fail
                                logger.info(f"[*] Hot-Reload: Cap nhat Max Failures -> {new_max_fail}")
                            if self.rate_limiter.lockout_duration_sec != new_lockout:
                                self.rate_limiter.lockout_duration_sec = new_lockout
                                logger.info(f"[*] Hot-Reload: Cap nhat Lockout Duration -> {new_lockout}s")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[-] Khong the doc file config {self.cfg_file}: {e}")

    def _save_enabled_state(self, enabled_state: bool):
        """Luu trang thai enabled vao config.json va cap nhat ngay trong RAM."""
        self.enabled = enabled_state
        if sys.platform != "win32":
            try:
                from platforms.linux.gdm_hook import sync_gdm_localization
                sync_gdm_localization(enabled_state)
            except Exception:
                pass
        if os.path.exists(self.cfg_file):
            try:
                with open(self.cfg_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg.setdefault("system", {})["enabled"] = enabled_state
                with open(self.cfg_file, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)
            except Exception as e:
                logger.warning(f"[-] Khong the luu trang thai enabled vao {self.cfg_file}: {e}")

    def _load_policy(self) -> dict:
        """Nap file policy theo ngu canh dich vu (sudo, screensaver, gdm, windows-unlock...)."""
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
        """Thuc hien xac thuc 1:1 voi Policy theo tung dich vu PAM / Windows Credential Provider."""
        # 0. Hot-reload trang thai enabled va policy tu file config
        self._reload_config()
        self.policy = self._load_policy()

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
        decision_thresh = svc_policy.get("decision_threshold", DEFAULT_DECISION_THRESHOLD)
        sim_thresh = svc_policy.get("sim_threshold", DEFAULT_SIM_THRESHOLD)
        require_active = svc_policy.get("require_active_liveness", True)
        timeout_sec = svc_policy.get("active_timeout_sec", self.verify_timeout_sec)

        logger.info(f"[AUDIT] Yeu cau xac thuc user='{user}' (Service='{service}', PID={peer_pid}, Thresh={decision_thresh})")

        # 5. Kiem tra template: Ho tro che do 1:N thong minh
        # Uu tien template cua dung user do; neu user chua co (hoac may doi ten),
        # tu dong nap toan bo tat ca cac template khuon mat da luu trong may
        all_templates = load_all_enrolled_templates()
        if not all_templates:
            logger.warning(f"[AUDIT] He thong chua dang ky bat ky khuon mat nao.")
            return {"status": "NOT_ENROLLED", "fallback": "PASSWORD"}

        # Xac dinh danh sach mau so khop
        user_specific = [item for item in all_templates if item[0].lower() == user.lower()]
        if user_specific:
            # Uu tien nguoi dung hien tai truoc, tiep theo la cac khuon mat khac trong may
            match_candidates = user_specific + [item for item in all_templates if item[0].lower() != user.lower()]
        else:
            match_candidates = all_templates

        logger.info(f"[*] San sang so khop 1:N voi {len(match_candidates)} khuon mat da luu trong may.")

        def send_progress(msg: str):
            """Gui huong dan truc tiep ve terminal cua client."""
            if client_conn:
                try:
                    payload = json.dumps({"event": "progress", "message": msg}, ensure_ascii=False) + "\n"
                    client_conn.sendall(payload.encode("utf-8"))
                except Exception:
                    pass

        # 6. Mo camera an toan voi Non-blocking Lock chong tranh chap giua cac thread
        if not self._auth_lock.acquire(blocking=False):
            logger.warning(f"[AUDIT] CAMERA_BUSY: Dang co phien xac thuc khac chay (PID {peer_pid}).")
            send_progress("Camera đang bận. Vui lòng sử dụng mật mã dự phòng")
            return {
                "status": "CAMERA_BUSY",
                "fallback": "PASSWORD",
                "message": "Camera đang bận. Vui lòng sử dụng mật mã dự phòng",
                "error": "CAMERA_BUSY"
            }

        try:
            self.anti_spoof.reset()
            with self.camera_mgr.acquire() as cap:
                logger.info(f"[*] Camera BAT thanh cong. Tien hanh quet khuon mat...")
                send_progress("Đang kết nối camera... Vui lòng nhìn thẳng vào màn hình")
                challenge = ChallengeResponseDetector(timeout_sec=timeout_sec, preload_hands=False)
                if require_active:
                    chosen_action = challenge.reset_challenge()
                    send_progress(f"{chosen_action.value}")
                t_start = time.time()

                best_decision = None
                active_passed = not require_active
                last_hint_sent = ""
                frontal_streak = 0

                while time.time() - t_start < timeout_sec:
                    ret, frame = cap.read()
                    if not ret or frame is None or frame.size == 0:
                        time.sleep(0.02)
                        continue

                    # Pipeline AI
                    detections = self.detector.detect(frame)
                    if len(detections) == 0:
                        frontal_streak = 0
                        continue

                    # Chon chu the chinh (Primary Subject): Sap xep theo dien tich Bounding Box giam dan de chon mat lon nhat (gan camera nhat)
                    # box dinh dang: (top, right, bottom, left) -> Dien tich = (bottom - top) * (right - left)
                    detections.sort(key=lambda d: (d[0][2] - d[0][0]) * (d[0][1] - d[0][3]), reverse=True)
                    box, landmarks, det_conf = detections[0]

                    # Kiem tra kich thuoc mat chu the chinh (tranh mat o qua xa / tap am)
                    face_h = box[2] - box[0]
                    face_w = box[1] - box[3]
                    if face_h < MIN_FACE_SIZE_PX or face_w < MIN_FACE_SIZE_PX:
                        frontal_streak = 0
                        continue
                    quality = self.fiqa.evaluate_quality(frame, box, landmarks)
                    pose = (quality.yaw, quality.pitch, quality.roll)

                    if not active_passed:
                        p, hint, _ = challenge.check_action(frame, pose, landmarks, box)
                        if hint and hint != last_hint_sent:
                            logger.info(f"[*] Thu thach liveness: {hint}")
                            send_progress(f"{hint}")
                            last_hint_sent = hint
                        if p:
                            active_passed = True
                            frontal_streak = 0
                            # Gia han them toi thieu 5.0s de nguoi dung thong tha quay mat ve chinh dien
                            t_start = max(t_start, time.time() - timeout_sec + POST_LIVENESS_WINDOW_SEC)
                            logger.info("[+] Thu thach liveness: HOAN THANH! Cho khuon mat ve chinh dien...")
                            send_progress("Xác thực cử động thành công! Vui lòng nhìn thẳng...")
                            continue

                    if active_passed:
                        pose_returned = True
                        if require_active and challenge._pose_base is not None and chosen_action in (
                            ActionType.LOOK_UP, ActionType.NOD_DOWN, ActionType.TURN_LEFT, ActionType.TURN_RIGHT
                        ):
                            byaw, bpitch = challenge._pose_base
                            cur_y, cur_p = getattr(challenge, "_pose_ema", None) or (quality.yaw, quality.pitch)
                            cur_y = 0.60 * quality.yaw + 0.40 * cur_y
                            cur_p = 0.60 * quality.pitch + 0.40 * cur_p
                            challenge._pose_ema = (cur_y, cur_p)
                            if abs(cur_y - byaw) > POSE_RETURN_MAX_DYAW or abs(cur_p - bpitch) > POSE_RETURN_MAX_DPITCH:
                                pose_returned = False

                        is_strict_frontal = quality.passed and abs(quality.yaw) <= STRICT_FRONTAL_MAX_YAW and abs(quality.pitch) <= STRICT_FRONTAL_MAX_PITCH and pose_returned
                        if not is_strict_frontal:
                            frontal_streak = 0
                            continue
                        frontal_streak += 1
                        if require_active and frontal_streak < FRONTAL_STREAK_REQUIRED:
                            continue
                        is_real, passive_score, _ = self.anti_spoof.predict_smoothed(frame, box)
                        emb = self.embedder.extract(frame, box, landmarks=landmarks)
                        
                        effective_passive = passive_score if quality.is_live_fft else (passive_score * MOIRE_PASSIVE_PENALTY)
                        fiqa_weight = (1.0 if quality.passed else LOW_FIQA_PENALTY) * (1.0 if quality.is_live_fft else MOIRE_FIQA_PENALTY)

                        # Duyet qua tat ca cac ung vien khuon mat hop le trong may (1:N Smart Matching)
                        for cand_name, cand_templates in match_candidates:
                            sim_pairs = []
                            for t in cand_templates:
                                d = self.embedder.compute_distance(emb, t)
                                sim_pairs.append((float(1.0 - d), t))
                            sim_pairs.sort(key=lambda x: x[0], reverse=True)
                            best_sim, best_t = sim_pairs[0]

                            if len(sim_pairs) >= 2:
                                effective_sim = TOP1_TEMPLATE_WEIGHT * best_sim + TOP2_TEMPLATE_WEIGHT * sim_pairs[1][0]
                            else:
                                effective_sim = best_sim

                            decision = self.fusion.evaluate_fused(
                                passive_score=effective_passive,
                                active_passed=True,
                                probe_emb=emb,
                                template_emb=best_t,
                                raw_cosine_sim=effective_sim,
                                best_user=cand_name,
                                fiqa_score=fiqa_weight,
                                sim_threshold=sim_thresh,
                                decision_threshold=decision_thresh,
                            )

                            if decision.success and decision.confidence_score >= decision_thresh:
                                best_decision = decision
                                logger.info(f"[+] Khop thanh cong voi khuon mat: '{cand_name}' (conf={decision.confidence_score:.3f})")
                                break

                        if best_decision and best_decision.success:
                            break

                # Ket qua xac thuc
                if best_decision and best_decision.success and best_decision.confidence_score >= decision_thresh:
                    self.rate_limiter.record_success(user)
                    cand_display = cand_name if 'cand_name' in locals() and cand_name else user
                    send_progress(f"Xác thực thành công ({cand_display})! Đang mở khóa...")
                    time.sleep(0.4) # Cho 400ms de man hinh LogonUI hien thi ro chu thong bao truoc khi dang nhap
                    logger.info(f"[AUDIT] AUTH_SUCCESS user='{user}' svc='{service}' conf={best_decision.confidence_score:.3f}")
                    return {
                        "status": "SUCCESS",
                        "user": user,
                        "confidence": float(best_decision.confidence_score),
                        "matched_user": cand_display
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
            send_progress("Camera đang bận hoặc không thể mở. Vui lòng sử dụng mật mã dự phòng")
            return {
                "status": "CAMERA_BUSY",
                "fallback": "PASSWORD",
                "message": "Camera đang bận hoặc không thể mở. Vui lòng sử dụng mật mã dự phòng",
                "error": "CAMERA_BUSY"
            }
        except CameraDeviceError as e:
            logger.warning(f"[AUDIT] CAMERA_DEVICE_ERROR tu PID {peer_pid}: {e}. Fallback password ngay.")
            send_progress("Không thể kết nối camera. Vui lòng sử dụng mật mã dự phòng")
            return {
                "status": "CAMERA_BUSY",
                "fallback": "PASSWORD",
                "message": "Không thể kết nối camera. Vui lòng sử dụng mật mã dự phòng",
                "error": "CAMERA_DEVICE_ERROR"
            }
        except VirtualCameraDetectedError as e:
            logger.error(f"[AUDIT] VIRTUAL_CAMERA_BLOCKED tu PID {peer_pid}: {e}")
            send_progress("Camera không hợp lệ. Vui lòng sử dụng mật mã dự phòng")
            return {
                "status": "CAMERA_BUSY",
                "fallback": "PASSWORD",
                "message": "Camera không hợp lệ. Vui lòng sử dụng mật mã dự phòng",
                "error": "VIRTUAL_CAMERA_BLOCKED"
            }
        except Exception as e:
            logger.error(f"[AUDIT] System Error / Camera Error: {e}")
            send_progress("Không thể mở camera. Vui lòng sử dụng mật mã dự phòng")
            return {
                "status": "CAMERA_BUSY",
                "fallback": "PASSWORD",
                "message": "Không thể mở camera. Vui lòng sử dụng mật mã dự phòng",
                "error": str(e)
            }
        finally:
            self._auth_lock.release()

    def handle_client(self, conn: socket.socket):
        """Xu ly mot phien ket noi IPC tu PAM module kem kiem tra SO_PEERCRED."""
        peer_pid, peer_uid, peer_gid = 0, 0, 0
        should_stop_daemon = False
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
            # Tren Linux, tien trinh gui AUTH chi duoc phep la root (UID 0 tu PAM),
            # cung UID voi tien trinh Daemon, hoac chinh la process cua user do dang chay.
            if sys.platform != "win32" and peer_uid not in (0, os.getuid()) and action == "auth":
                import pwd
                try:
                    caller_name = pwd.getpwuid(peer_uid).pw_name
                    if caller_name.lower() != user.lower():
                        logger.warning(f"[SECURITY] Process PID {peer_pid} (UID {peer_uid}:{caller_name}) co tinh goi AUTH cho user '{user}'! BI CHAN.")
                        conn.sendall(json.dumps({"status": "PERMISSION_DENIED", "fallback": "PASSWORD"}).encode("utf-8") + b"\n")
                        return
                except Exception:
                    pass

            # Xu ly cac lenh quan tri (status, enable, disable, reload, stop)
            if action == "status":
                self._reload_config()
                resp = {
                    "status": "OK",
                    "pid": os.getpid(),
                    "os": self.os_type,
                    "enabled": self.enabled,
                    "mode": self.mode
                }
            elif action == "enable":
                self._save_enabled_state(True)
                logger.info(f"[AUDIT] Cong tac FaceID da duoc BAT (ENABLED) qua IPC (PID: {peer_pid}).")
                resp = {"status": "OK", "enabled": True, "message": "FaceID ENABLED"}
            elif action == "disable":
                self._save_enabled_state(False)
                logger.info(f"[AUDIT] Cong tac FaceID da duoc TAT (DISABLED/BYPASS) qua IPC (PID: {peer_pid}).")
                resp = {"status": "OK", "enabled": False, "message": "FaceID DISABLED"}
            elif action == "reload":
                self._reload_config()
                self.policy = self._load_policy()
                logger.info(f"[AUDIT] Da tai lai cau hinh config.json & policy.json (enabled={self.enabled}).")
                resp = {"status": "OK", "enabled": self.enabled}
            elif action == "stop":
                logger.info(f"[AUDIT] Nhan lenh DUNG DAEMON (STOP) qua IPC (PID: {peer_pid}).")
                resp = {"status": "STOPPING", "pid": os.getpid(), "message": "FaceID Daemon dang dung..."}
                should_stop_daemon = True
            elif self.rate_limiter.check_flood_attack():
                resp = {"status": "RATE_LIMITED", "fallback": "PASSWORD"}
            elif action == "auth" and user:
                resp = self.authenticate_user(user, service=service, peer_pid=peer_pid, peer_uid=peer_uid, client_conn=conn)
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
            if should_stop_daemon:
                threading.Thread(target=self.stop, daemon=True).start()

    def _accept_loop(self, sock: socket.socket):
        while self.running:
            try:
                conn, _ = sock.accept()
                client_thread = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                client_thread.start()
            except Exception as e:
                if self.running:
                    logger.error(f"Loi chap nhan ket noi: {e}")

    def start(self):
        """Khoi chay Server Socket va luu PID."""
        # Luu PID file de faceidctl quan ly
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.pid_file)), exist_ok=True)
            with open(self.pid_file, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            if sys.platform != "win32":
                try:
                    os.chmod(self.pid_file, 0o666)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"[-] Khong the ghi PID file {self.pid_file}: {e}")

        self._tcp_sock: Optional[socket.socket] = None

        if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
            sock_dir = os.path.dirname(os.path.abspath(self.socket_path))
            try:
                os.makedirs(sock_dir, exist_ok=True)
                if os.path.exists(self.socket_path):
                    os.remove(self.socket_path)
                self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._server_sock.bind(self.socket_path)
                os.chmod(self.socket_path, 0o666)
            except (PermissionError, OSError):
                # Fallback sang /tmp/faceid.sock neu chay bang user thuong khong co quyen ghi /run/faceid
                self.socket_path = "/tmp/faceid.sock"
                if os.path.exists(self.socket_path):
                    try:
                        os.remove(self.socket_path)
                    except OSError:
                        pass
                self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._server_sock.bind(self.socket_path)
                try:
                    os.chmod(self.socket_path, 0o666)
                except OSError:
                    pass
            self._server_sock.listen(16)
            logger.info(f"[+] Lang nghe Unix Socket tai: {self.socket_path}")

            # Mo them cong TCP loopback 127.0.0.1:9876 tren Linux de ho tro GUI / Fuzz test / Dual-mode
            try:
                self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._tcp_sock.bind(("127.0.0.1", self.windows_port))
                self._tcp_sock.listen(16)
                logger.info(f"[+] Lang nghe song song TCP Loopback tai: 127.0.0.1:{self.windows_port}")
            except Exception as e:
                logger.warning(f"[-] Khong the mo cong TCP phu {self.windows_port}: {e}")
                self._tcp_sock = None
        else:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(("127.0.0.1", self.windows_port))
            self._server_sock.listen(16)
            logger.info(f"[+] Lang nghe TCP Socket tai: 127.0.0.1:{self.windows_port} (Windows Dev Mode)")

        self.running = True
        if self._tcp_sock is not None:
            threading.Thread(target=self._accept_loop, args=(self._tcp_sock,), daemon=True).start()

        self._accept_loop(self._server_sock)

    def stop(self):
        """Dung daemon va don dep socket / PID file."""
        self.running = False
        for s in (self._server_sock, getattr(self, "_tcp_sock", None)):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        if os.path.exists(self.socket_path) and sys.platform != "win32":
            try:
                os.remove(self.socket_path)
            except OSError:
                pass
        if os.path.exists(self.pid_file):
            try:
                os.remove(self.pid_file)
            except OSError:
                pass
        logger.info("[*] FaceID Daemon da dung an toan.")
