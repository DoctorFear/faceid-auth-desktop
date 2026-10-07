import os
import numpy as np
from dataclasses import dataclass


@dataclass
class FusionDecision:
    success: bool
    is_live: bool
    matched_user: str | None
    raw_similarity: float
    calibrated_similarity: float
    calibrated_liveness_score: float
    confidence_score: float
    reason: str


class MultiModalFusionEngine:
    def __init__(
        self,
        cohort_path: str | None = None,
        cohort_size: int = 128,
        embedding_dim: int = 512,
        temperature: float = 1.65,
        sim_threshold: float = 0.62,
        decision_threshold: float = 0.65
    ):
        self.temperature = temperature
        self.sim_threshold = sim_threshold
        self.decision_threshold = decision_threshold
        self.embedding_dim = embedding_dim

        self.cohort_embeddings = self._init_cohort(cohort_path, cohort_size, embedding_dim)
        
        # Trong so Logistic Regression: [calib_passive, snorm_sim, fiqa_score, active_bias]
        self.weights = np.array([2.8, 3.2, 0.8, 2.0], dtype=np.float32)
        self.intercept = -4.2

    def _init_cohort(self, path: str | None, size: int, dim: int) -> np.ndarray:
        if path and os.path.exists(path):
            cohort = np.load(path)
            if cohort.ndim == 2 and cohort.shape[1] == dim:
                norms = np.linalg.norm(cohort, axis=1, keepdims=True) + 1e-9
                return cohort / norms

        rng = np.random.default_rng(seed=42)
        random_cohort = rng.standard_normal((size, dim), dtype=np.float32)
        norms = np.linalg.norm(random_cohort, axis=1, keepdims=True) + 1e-9
        return random_cohort / norms

    def calibrate_passive_score(self, raw_score: float) -> float:
        clipped = np.clip(raw_score, 1e-5, 1.0 - 1e-5)
        logit = np.log(clipped / (1.0 - clipped))
        calibrated = 1.0 / (1.0 + np.exp(-logit / self.temperature))
        return float(calibrated)

    def compute_snorm(self, probe_emb: np.ndarray, template_emb: np.ndarray, raw_sim: float) -> float:
        p_emb = probe_emb.reshape(1, -1)
        t_emb = template_emb.reshape(1, -1)

        probe_cohort_sims = np.dot(self.cohort_embeddings, p_emb.T).flatten()
        mu_p = float(np.mean(probe_cohort_sims))
        sigma_p = float(np.std(probe_cohort_sims)) + 1e-7

        template_cohort_sims = np.dot(self.cohort_embeddings, t_emb.T).flatten()
        mu_t = float(np.mean(template_cohort_sims))
        sigma_t = float(np.std(template_cohort_sims)) + 1e-7

        s_p = (raw_sim - mu_p) / sigma_p
        s_t = (raw_sim - mu_t) / sigma_t
        s_norm = (s_p + s_t) / 2.0

        return float(1.0 / (1.0 + np.exp(-0.7 * s_norm)))

    def evaluate_fused(
        self,
        passive_score: float,
        active_passed: bool,
        probe_emb: np.ndarray | None,
        template_emb: np.ndarray | None,
        raw_cosine_sim: float,
        best_user: str | None,
        fiqa_score: float = 1.0
    ) -> FusionDecision:
        calib_passive = self.calibrate_passive_score(passive_score)

        if probe_emb is not None and template_emb is not None:
            calib_sim = self.compute_snorm(probe_emb, template_emb, raw_cosine_sim)
        else:
            calib_sim = raw_cosine_sim

        if not active_passed:
            return FusionDecision(
                success=False,
                is_live=False,
                matched_user=None,
                raw_similarity=raw_cosine_sim,
                calibrated_similarity=calib_sim,
                calibrated_liveness_score=calib_passive,
                confidence_score=0.0,
                reason="Chua hoan thanh thu thach Active Liveness"
            )

        features = np.array([
            calib_passive,
            calib_sim,
            float(np.clip(fiqa_score, 0.0, 1.0)),
            1.0 if active_passed else 0.0
        ], dtype=np.float32)

        z = np.dot(self.weights, features) + self.intercept
        confidence = float(1.0 / (1.0 + np.exp(-z)))

        if calib_passive < 0.45:
            return FusionDecision(
                success=False,
                is_live=False,
                matched_user=None,
                raw_similarity=raw_cosine_sim,
                calibrated_similarity=calib_sim,
                calibrated_liveness_score=calib_passive,
                confidence_score=confidence,
                reason=f"Phat hien gia mao (Passive score: {calib_passive:.2f})"
            )

        if raw_cosine_sim < self.sim_threshold:
            return FusionDecision(
                success=False,
                is_live=True,
                matched_user=None,
                raw_similarity=raw_cosine_sim,
                calibrated_similarity=calib_sim,
                calibrated_liveness_score=calib_passive,
                confidence_score=confidence,
                reason=f"Do tuong dong chua dat nguong ({raw_cosine_sim:.2f} < {self.sim_threshold:.2f})"
            )

        if confidence >= self.decision_threshold and best_user:
            return FusionDecision(
                success=True,
                is_live=True,
                matched_user=best_user,
                raw_similarity=raw_cosine_sim,
                calibrated_similarity=calib_sim,
                calibrated_liveness_score=calib_passive,
                confidence_score=confidence,
                reason="Xac thuc thanh cong toan dien"
            )

        return FusionDecision(
            success=False,
            is_live=True,
            matched_user=None,
            raw_similarity=raw_cosine_sim,
            calibrated_similarity=calib_sim,
            calibrated_liveness_score=calib_passive,
            confidence_score=confidence,
            reason=f"Diem tin cay tong hop chua dat nguong ({confidence:.2f} < {self.decision_threshold:.2f})"
        )
