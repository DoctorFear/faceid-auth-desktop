#!/usr/bin/env python3 moi
"""
FaceID Authentication Daemon Entrypoint
Dung de khoi chay tien trinh FaceID Daemon chay ngam tren he thong Linux (systemd service).
"""

import os
import sys
import signal
import argparse

# Dam bao import duoc cac module trong thu muc goc cua du an
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from platforms.linux.socket_server import FaceIDDaemon, DEFAULT_SOCKET_PATH


def main():
    parser = argparse.ArgumentParser(description="FaceID Hybrid Authentication Daemon")
    parser.add_argument("--socket", type=str, default=DEFAULT_SOCKET_PATH, help="Duong dan Unix Domain Socket")
    parser.add_argument("--timeout", type=float, default=6.0, help="Thoi gian xac thuc toi da moi phien (giay)")
    parser.add_argument("--max-failures", type=int, default=3, help="So lan fail toi da truoc khi lockout")
    parser.add_argument("--lockout-sec", type=float, default=45.0, help="Thoi gian khoa tam thoi (giay)")
    args = parser.parse_args()

    daemon = FaceIDDaemon(
        socket_path=args.socket,
        max_failures=args.max_failures,
        lockout_duration_sec=args.lockout_sec,
        verify_timeout_sec=args.timeout
    )

    def sig_handler(sig, frame):
        print("\n[*] Nhan tin hieu dung (SIGINT/SIGTERM), dang giai phong tai nguyen...")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, sig_handler)

    print(f"[*] Dang khoi dong FaceID Daemon (PID: {os.getpid()})...")
    daemon.start()


if __name__ == "__main__":
    main()
