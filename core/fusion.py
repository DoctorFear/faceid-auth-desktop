from dataclasses import dataclass
import numpy as np

@dataclass
class AuthDecision:
    success: bool
    matched_user: str | None
    similarity: float
    is_live: bool
    passive_score: float
    active_passed: bool
    reason: str

class MultiModalFusionEngine:
    """Ket hop MiniFASNetV2 (Passive), Challenge-Response (Active) va ArcFace."""

    def __init__(self, sim_threshold: float = 0.64, passive_threshold: float = 0.60):
        # sim_threshold: 1 - cosine_distance >= 0.64 (tuong duong distance <= 0.36)
        self.sim_threshold = sim_threshold
        self.passive_threshold = passive_threshold

    def evaluate(
        self,
        passive_score: float,
        active_passed: bool,
        best_user: str | None,
        min_cosine_dist: float
    ) -> AuthDecision:
        similarity = max(0.0, 1.0 - min_cosine_dist)

        # 1. Kiem tra Passive Liveness (MiniFASNet)
        if passive_score < self.passive_threshold:
            return AuthDecision(
                success=False,
                matched_user=None,
                similarity=similarity,
                is_live=False,
                passive_score=passive_score,
                active_passed=active_passed,
                reason="Phat hien gia mao (Man hinh / Anh in)"
            )

        # 2. Kiem tra Active Liveness (Challenge-Response)
        if not active_passed:
            return AuthDecision(
                success=False,
                matched_user=None,
                similarity=similarity,
                is_live=False,
                passive_score=passive_score,
                active_passed=False,
                reason="Chua vuot qua thu thach tuong tac"
            )

        # 3. Kiem tra do khop nhan dien ArcFace
        if best_user is None or similarity < self.sim_threshold:
            return AuthDecision(
                success=False,
                matched_user=None,
                similarity=similarity,
                is_live=True,
                passive_score=passive_score,
                active_passed=True,
                reason=f"Khuon mat khong khop (Do giong: {similarity*100:.1f}%)"
            )

        # Hop le tat ca cac tieu chi
        return AuthDecision(
            success=True,
            matched_user=best_user,
            similarity=similarity,
            is_live=True,
            passive_score=passive_score,
            active_passed=True,
            reason=f"Xac thuc thanh cong [{best_user}]"
        )
