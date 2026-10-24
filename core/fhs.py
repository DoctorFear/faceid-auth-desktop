import os
import sys
import re

try:
    import pwd
except ImportError:
    pwd = None
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Regex kiem tra tinh hop le cua username theo chuan POSIX/Linux
# Chi cho phep chu cai thuong, so, gach duoi, gach ngang (toi da 32 ky tu)
# Ngan chan tuyet doi tan cong Path Traversal (vi du: "../../etc/passwd")
USERNAME_REGEX = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def validate_username(username: str) -> bool:
    """Kiem tra username hop le theo chuan POSIX, ngan chan Path Traversal va ky tu doc hai."""
    if not username or not isinstance(username, str):
        return False
    return bool(USERNAME_REGEX.match(username))


def get_fhs_path(sub_path: str, category: str = "data") -> str:
    """
    Anh xa duong dan linh hoat giua chuan FHS Linux (/etc, /var/lib, /run)
    va thu muc project tren Windows / Dev local.
    
    Category:
      - 'config' : /etc/faceid (Linux Prod) hoac <BASE_DIR>/config (Dev)
      - 'data'   : /var/lib/faceid (Linux Prod) hoac <BASE_DIR>/data (Dev)
      - 'run'    : /run/faceid (Linux Prod) hoac <BASE_DIR>/data (Dev)
      - 'models' : /usr/share/faceid/models (Linux Prod) hoac <BASE_DIR>/models (Dev)
    """
    if sys.platform != "win32" and os.path.exists("/etc/faceid"):
        # Moi truong Linux Production da cai dat FHS
        fhs_roots = {
            "config": "/etc/faceid",
            "data": "/var/lib/faceid",
            "run": "/run/faceid",
            "models": "/usr/share/faceid/models"
        }
        root = fhs_roots.get(category, "/var/lib/faceid")
        return os.path.join(root, sub_path)

    # Moi truong Dev local (Windows hoac Linux dev)
    dev_roots = {
        "config": os.path.join(BASE_DIR, "config"),
        "data": os.path.join(BASE_DIR, "data"),
        "run": os.path.join(BASE_DIR, "data"),
        "models": os.path.join(BASE_DIR, "models")
    }
    root = dev_roots.get(category, os.path.join(BASE_DIR, "data"))
    return os.path.join(root, sub_path)
