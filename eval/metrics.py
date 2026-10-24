"""
Chỉ số đánh giá sinh trắc học theo tiêu chuẩn ISO/IEC 19795-1 và ISO/IEC 30107-3 (PAD):
- FAR (False Acceptance Rate) / FMR (False Match Rate)
- FRR (False Rejection Rate) / FNMR (False Non-Match Rate)
- EER (Equal Error Rate)
- APCER (Attack Presentation Classification Error Rate)
- BPCER (Bona Fide Presentation Classification Error Rate)
"""

import numpy as np
from sklearn.metrics import roc_curve, auc


def compute_far_frr_eer(genuine_scores: np.ndarray, imposter_scores: np.ndarray) -> dict:
    """Tính toán đường cong ROC và chỉ số Equal Error Rate (EER) giữa điểm Genuine và Imposter."""
    labels = np.concatenate([np.ones_like(genuine_scores), np.zeros_like(imposter_scores)])
    scores = np.concatenate([genuine_scores, imposter_scores])

    fpr, tpr, thresholds = roc_curve(labels, scores)
    fnr = 1.0 - tpr

    # Tìm điểm giao nhau EER nơi FPR ≈ FNR
    eer_idx = np.nanargmin(np.abs(fpr - fnr))
    eer = float((fpr[eer_idx] + fnr[eer_idx]) / 2.0)
    eer_threshold = float(thresholds[eer_idx])
    roc_auc = float(auc(fpr, tpr))

    # Tìm TAR tại FAR = 0.1% (0.001)
    far_target = 0.001
    idx_target = np.where(fpr <= far_target)[0]
    tar_at_far_001 = float(tpr[idx_target[-1]]) if len(idx_target) > 0 else 0.0

    return {
        "eer": eer,
        "eer_threshold": eer_threshold,
        "auc": roc_auc,
        "tar_at_far_0.1%": tar_at_far_001,
        "fpr": fpr,
        "tpr": tpr,
        "thresholds": thresholds
    }


def compute_pad_metrics(bona_fide_scores: np.ndarray, attack_scores: np.ndarray, threshold: float = 0.5) -> dict:
    """
    Tính toán chỉ số chống giả mạo theo chuẩn ISO/IEC 30107-3:
    - APCER: Tỷ lệ mẫu tấn công bị nhận nhầm là người thật (Attack Presentation Classification Error Rate).
    - BPCER: Tỷ lệ mẫu người thật bị từ chối oan (Bona Fide Presentation Classification Error Rate).
    """
    # Mẫu tấn công (Attack): Điểm >= threshold bị coi là lọt (APCER)
    apcer = float(np.mean(attack_scores >= threshold))

    # Mẫu người thật (Bona Fide): Điểm < threshold bị coi là chặn oan (BPCER)
    bpcer = float(np.mean(bona_fide_scores < threshold))

    acer = (apcer + bpcer) / 2.0

    return {
        "threshold": threshold,
        "apcer": apcer,
        "bpcer": bpcer,
        "acer": acer
    }
