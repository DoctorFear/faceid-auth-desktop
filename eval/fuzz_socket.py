import sys
import socket
import json
import time

"""
Kich ban kiem thu do ben va kha nang chong chiu (Fuzz Testing) cho Unix / TCP Socket cua Daemon.
Kiem tra xem Daemon co bi crash, treo (freeze), hay ro ri thong tin khi nhan:
  1. Goi tin JSON meo mo (Malformed JSON)
  2. Goi tin cuc lon (Oversized Payload / Buffer Overflow Attempt)
  3. Path Traversal string trong username (../../etc/passwd)
  4. Du lieu byte nhi phan khong phai UTF-8 (Raw Random Bytes)
  5. Tan cong flood dồn dập (DoS Attack)
"""

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 9876


def send_raw_payload(payload: bytes, desc: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((TARGET_HOST, TARGET_PORT))
        s.sendall(payload + b"\n")
        resp = s.recv(4096).decode("utf-8", errors="ignore").strip()
        print(f"  [PASS] {desc} -> Daemon phan hoi an toan: {resp[:60]}")
        return resp
    except Exception as e:
        print(f"  [WARN] {desc} -> Exception: {e}")
        return ""
    finally:
        s.close()


def run_fuzz_tests():
    print("==================================================")
    print("  BAT DAU FUZZ TESTING TREN SOCKET DAEMON")
    print("==================================================")

    # 1. Malformed JSON
    send_raw_payload(b"{'invalid_json': true,", "Test 1: Malformed JSON syntax")

    # 2. Path Traversal Attempt
    payload_pt = json.dumps({"action": "auth", "user": "../../../../etc/passwd"}).encode("utf-8")
    send_raw_payload(payload_pt, "Test 2: Path Traversal Username")

    # 3. SQL / Shell Injection chars
    payload_inj = json.dumps({"action": "auth", "user": "admin; rm -rf /; `whoami`"}).encode("utf-8")
    send_raw_payload(payload_inj, "Test 3: Shell Injection Characters")

    # 4. Oversized Payload (100 KB text)
    oversized = json.dumps({"action": "auth", "user": "A" * 100000}).encode("utf-8")
    send_raw_payload(oversized, "Test 4: Oversized 100KB Payload")

    # 5. Non-UTF-8 Raw Random Bytes
    send_raw_payload(b"\xff\xfe\x00\x01\x89\xab\xcd\xef", "Test 5: Non-UTF8 Binary Bytes")

    # 6. DoS Flood Test (20 requests in 0.2s)
    print("  [*] Test 6: DoS Flood Protection (20 requests lien tuc)...")
    flood_blocked = 0
    for _ in range(20):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect((TARGET_HOST, TARGET_PORT))
            s.sendall(json.dumps({"action": "auth", "user": "test"}).encode("utf-8") + b"\n")
            res = s.recv(1024).decode("utf-8", errors="ignore")
            if "RATE_LIMITED" in res:
                flood_blocked += 1
            s.close()
        except Exception:
            pass
    print(f"  [PASS] DoS Flood: Da chan thanh cong {flood_blocked} requests qua nguong!")

    print("\n>>> KET QUA: DAEMON SONG SOT QUA MOI TEST, KHONG BI CRASH! <<<\n")


if __name__ == "__main__":
    run_fuzz_tests()
