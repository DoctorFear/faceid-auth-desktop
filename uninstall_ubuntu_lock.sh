#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXE="$SCRIPT_DIR/venv/bin/python"
if [ ! -x "$PYTHON_EXE" ]; then
    PYTHON_EXE="$(command -v python3)"
fi
CTL_SCRIPT="$SCRIPT_DIR/scripts/faceidctl"

if [ "$EUID" -ne 0 ]; then
    if command -v pkexec >/dev/null 2>&1; then
        exec pkexec bash "$SCRIPT_DIR/uninstall_ubuntu_lock.sh" "$@"
    else
        exec sudo bash "$SCRIPT_DIR/uninstall_ubuntu_lock.sh" "$@"
    fi
fi

echo "[*] Dang go bo FaceID PAM khoi /etc/pam.d/common-auth..."
PAM_FILE="/etc/pam.d/common-auth"
if [ -f "$PAM_FILE" ]; then
    sed -i "\|faceid_pam_client.py|d" "$PAM_FILE"
    sed -i "s|auth\t\[success=3 default=ignore\]\tpam_unix\.so|auth\t[success=2 default=ignore]\tpam_unix.so|" "$PAM_FILE"
    sed -i "s|auth\t\[success=2 default=ignore\]\tpam_sss\.so|auth\t[success=1 default=ignore]\tpam_sss.so|" "$PAM_FILE"
fi
"$PYTHON_EXE" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); from platforms.linux.gdm_hook import remove_gdm_localization; remove_gdm_localization()" 2>/dev/null || true
rm -f /etc/faceid/pam_installed

echo "[*] Dang dung va go bo systemd service faceid-auth.service..."
systemctl stop faceid-auth.service 2>/dev/null || true
systemctl disable faceid-auth.service 2>/dev/null || true
rm -f /etc/systemd/system/faceid-auth.service
systemctl daemon-reload 2>/dev/null || true

"$PYTHON_EXE" "$CTL_SCRIPT" stop 2>/dev/null || true
rm -f /run/faceid/faceid.sock /tmp/faceid.sock

echo "[+] Da go dang ky Ubuntu PAM Lock & Auto-Start thanh cong!"
