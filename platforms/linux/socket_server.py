"""
Wrapper tương thích ngược (Backward Compatibility).
Toàn bộ logic máy chủ Socket đã được quy hoạch vào module dùng chung đa nền tảng: core/server.py.
"""
from core.server import FaceIDDaemon, DEFAULT_SOCKET_PATH, get_peer_credentials, verify_file_sha256

__all__ = ["FaceIDDaemon", "DEFAULT_SOCKET_PATH", "get_peer_credentials", "verify_file_sha256"]
