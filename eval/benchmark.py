"""
benchmark.py - Do luong thoi gian suy dien (Inference Latency) va Thong luong (FPS)
cua tung module trong FaceID Pipeline theo tieu chuan hoc thuat.
"""

import os
import sys
import time
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from core.detector import YuNetDetector
from core.fiqa import FaceQualityAssessor
from core.anti_spoof import MiniFASNetDetector
from core.embedder import ArcFaceEmbedder
from core.fusion import MultiModalFusionEngine


def run_benchmark(iterations: int = 50):
    print("=" * 65)
    print("  FACEID HYBRID - BENCHMARK HIEU NANG & DO TRE AI PIPELINE")
    print(f"  (Chay thuc nghiem {iterations} vong lap / Model tren CPU/ONNX)")
    print("=" * 65)

    # 1. Khoi tao models
    print("[*] Dang khoi tao va nap cac model ONNX vao RAM...")
    detector = YuNetDetector()
    fiqa = FaceQualityAssessor()
    anti_spoof = MiniFASNetDetector()
    embedder = ArcFaceEmbedder()
    fusion = MultiModalFusionEngine()
    print("[+] Toan bo models da san sang.\n")

    # Frame gia lap 640x480
    dummy_frame = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
    dummy_box = (100, 350, 400, 150)
    dummy_landmarks = np.array([
        [200, 220], [300, 220], [250, 280], [220, 340], [280, 340]
    ], dtype=np.float32)

    # Warm-up
    for _ in range(5):
        detector.detect(dummy_frame)
        fiqa.evaluate_quality(dummy_frame, dummy_box, dummy_landmarks)
        anti_spoof.predict_smoothed(dummy_frame, dummy_box)
        embedder.extract(dummy_frame, dummy_box, dummy_landmarks)

    latencies = {}

    # Benchmark YuNet Detector
    t_start = time.perf_counter()
    for _ in range(iterations):
        detector.detect(dummy_frame)
    latencies["YuNet Detection"] = (time.perf_counter() - t_start) / iterations * 1000

    # Benchmark FIQA & 2D FFT Moire
    t_start = time.perf_counter()
    for _ in range(iterations):
        fiqa.evaluate_quality(dummy_frame, dummy_box, dummy_landmarks)
    latencies["FIQA & 2D FFT Moire"] = (time.perf_counter() - t_start) / iterations * 1000

    # Benchmark MiniFASNet v2 (PAD)
    t_start = time.perf_counter()
    for _ in range(iterations):
        anti_spoof.predict_smoothed(dummy_frame, dummy_box)
    latencies["MiniFASNet v2 (PAD)"] = (time.perf_counter() - t_start) / iterations * 1000

    # Benchmark ArcFace 512-D
    t_start = time.perf_counter()
    for _ in range(iterations):
        embedder.extract(dummy_frame, dummy_box, dummy_landmarks)
    latencies["ArcFace Embedding"] = (time.perf_counter() - t_start) / iterations * 1000

    # Benchmark S-Norm & Fusion Engine
    dummy_emb1 = np.random.randn(512).astype(np.float32)
    dummy_emb1 /= np.linalg.norm(dummy_emb1)
    dummy_emb2 = np.random.randn(512).astype(np.float32)
    dummy_emb2 /= np.linalg.norm(dummy_emb2)

    t_start = time.perf_counter()
    for _ in range(iterations):
        fusion.evaluate_fused(
            passive_score=0.95,
            active_passed=True,
            probe_emb=dummy_emb1,
            template_emb=dummy_emb2,
            raw_cosine_sim=0.75,
            best_user="test",
            fiqa_score=1.0
        )
    latencies["S-Norm & Fusion Engine"] = (time.perf_counter() - t_start) / iterations * 1000

    total_pipeline_latency = sum(latencies.values())
    fps_estimate = 1000.0 / total_pipeline_latency

    print(f"{'Module / Thanh phan':<30} | {'Do tre trung binh':<18} | {'Ty le %':<10}")
    print("-" * 65)
    for name, lat in latencies.items():
        pct = (lat / total_pipeline_latency) * 100
        print(f"{name:<30} | {lat:6.2f} ms         | {pct:5.1f}%")
    print("-" * 65)
    print(f"{'TONG DO TRE TOAN BO PIPELINE':<30} | {total_pipeline_latency:6.2f} ms         | 100.0%")
    print(f"{'TOC DO KHA THI (FPS UOC TINH)':<30} | ~{fps_estimate:5.1f} FPS")
    print("=" * 65)


if __name__ == "__main__":
    run_benchmark(iterations=50)
