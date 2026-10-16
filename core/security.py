import time
import threading
from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class LockoutStatus:
    is_locked: bool
    remaining_seconds: float
    consecutive_failures: int
    reason: str


class RateLimiter:
    """
    Co che Chong vet can (Anti-Brute-Force & Rate Limiting) cho FaceID Daemon:
    - Theo doi so lan xac thuc that bai tren tung user (Per-User Lockout).
    - Sau max_failures (mac dinh 3-5 lan) that bai lien tiep: Khoa FaceID cua user trong lockout_duration_sec (30-60 giay).
    - Chong tan cong tu choi dich vu (DoS Flood Protection) tren Unix Domain Socket: Gioi han tan so goi tin tu client.
    - Thread-safe: Dung threading.Lock() dong bo hoa giua cac tien trinh worker.
    """

    def __init__(
        self,
        max_failures: int = 3,
        lockout_duration_sec: float = 45.0,
        flood_window_sec: float = 1.0,
        max_requests_per_window: int = 5
    ):
        self.max_failures = max_failures
        self.lockout_duration_sec = lockout_duration_sec
        self.flood_window_sec = flood_window_sec
        self.max_requests_per_window = max_requests_per_window

        self._lock = threading.Lock()
        # Per-user state: user -> {"failures": int, "locked_until": float, "last_attempt": float}
        self._user_states: Dict[str, dict] = {}
        # Global request timestamps for flood/DoS detection
        self._request_history: list[float] = []

    def check_flood_attack(self) -> bool:
        """Kiem tra DoS Flood: Neu co qua nhieu request gui toi socket trong 1 giay -> Chan ngay."""
        now = time.time()
        with self._lock:
            # Loai bo cac timestamp cu ngoai flood_window
            self._request_history = [t for t in self._request_history if now - t < self.flood_window_sec]
            if len(self._request_history) >= self.max_requests_per_window:
                return True  # Flood detected!
            self._request_history.append(now)
            return False

    def get_lockout_status(self, user: str) -> LockoutStatus:
        """Kiem tra xem user hien tai co dang bi khoa xac thuc FaceID khong."""
        now = time.time()
        with self._lock:
            state = self._user_states.get(user)
            if not state:
                return LockoutStatus(
                    is_locked=False,
                    remaining_seconds=0.0,
                    consecutive_failures=0,
                    reason="OK"
                )

            locked_until = state.get("locked_until", 0.0)
            if now < locked_until:
                remaining = round(locked_until - now, 1)
                return LockoutStatus(
                    is_locked=True,
                    remaining_seconds=remaining,
                    consecutive_failures=state.get("failures", 0),
                    reason=f"Tai khoan '{user}' tam khoa FaceID do fail qua {self.max_failures} lan. Thu lai sau {remaining}s hoac dung password."
                )

            # Neu het thoi gian khoa, mo khoa nhung giu so failures o nguong an toan
            if locked_until > 0.0 and now >= locked_until:
                state["locked_until"] = 0.0
                state["failures"] = max(0, self.max_failures - 1)

            return LockoutStatus(
                is_locked=False,
                remaining_seconds=0.0,
                consecutive_failures=state.get("failures", 0),
                reason="OK"
            )

    def record_failure(self, user: str) -> LockoutStatus:
        """Ghi nhan 1 lan xac thuc that bai. Neu vuot qua max_failures, bat dau dem nguoc Lockout."""
        now = time.time()
        with self._lock:
            if user not in self._user_states:
                self._user_states[user] = {"failures": 0, "locked_until": 0.0, "last_attempt": now}

            state = self._user_states[user]
            state["failures"] += 1
            state["last_attempt"] = now

            if state["failures"] >= self.max_failures:
                state["locked_until"] = now + self.lockout_duration_sec
                remaining = self.lockout_duration_sec
                return LockoutStatus(
                    is_locked=True,
                    remaining_seconds=remaining,
                    consecutive_failures=state["failures"],
                    reason=f"Tai khoan '{user}' bi khoa FaceID trong {self.lockout_duration_sec}s do fail lien tiep {state['failures']} lan."
                )

            return LockoutStatus(
                is_locked=False,
                remaining_seconds=0.0,
                consecutive_failures=state["failures"],
                reason=f"Xac thuc that bai ({state['failures']}/{self.max_failures} lan cho phep)."
            )

    def record_success(self, user: str) -> None:
        """Xac thuc thanh cong -> Reset bo dem that bai ve 0."""
        with self._lock:
            if user in self._user_states:
                self._user_states[user] = {
                    "failures": 0,
                    "locked_until": 0.0,
                    "last_attempt": time.time()
                }

    def reset_user(self, user: str) -> None:
        """Quan tri vien hoac PAM reset trang thai cua user."""
        with self._lock:
            if user in self._user_states:
                del self._user_states[user]
