#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXE="$SCRIPT_DIR/venv/bin/python"
if [ ! -x "$PYTHON_EXE" ]; then
    PYTHON_EXE="$(command -v python3)"
fi
PAM_CLIENT="$SCRIPT_DIR/scripts/faceid_pam_client.py"
CTL_SCRIPT="$SCRIPT_DIR/scripts/faceidctl"
DAEMON_SCRIPT="$SCRIPT_DIR/daemon.py"

if [ "$EUID" -ne 0 ]; then
    if command -v pkexec >/dev/null 2>&1; then
        exec pkexec bash "$SCRIPT_DIR/install_ubuntu_lock.sh" "$@"
    else
        exec sudo bash "$SCRIPT_DIR/install_ubuntu_lock.sh" "$@"
    fi
fi

echo "=========================================================="
echo "  CAI DAT UBUNTU PAM FACEID LOCK & AUTO-START DAEMON"
echo "=========================================================="

# 0. Dung daemon user cu (neu co) de systemd quan ly tu luc boot man hinh Login
"$PYTHON_EXE" "$CTL_SCRIPT" stop 2>/dev/null || true

# Dam bao quyen doc/ghi cho thu muc du an de ca root (GDM Login) va user deu truy cap duoc
chmod o+rx "$(dirname "$(dirname "$SCRIPT_DIR")")" "$(dirname "$SCRIPT_DIR")" "$SCRIPT_DIR" 2>/dev/null || true
chmod -R a+rwX "$SCRIPT_DIR/data" "$SCRIPT_DIR/config" 2>/dev/null || true

# 1. Tich hop PAM module vao /etc/pam.d/common-auth (truoc pam_unix.so va truoc pam_deny.so)
PAM_FILE="/etc/pam.d/common-auth"
PAM_LINE_AUTH="auth	[success=4 default=ignore]	pam_exec.so quiet stdout $PYTHON_EXE $PAM_CLIENT"
PAM_LINE_FAIL="auth	[default=ignore]	pam_exec.so quiet stdout $PYTHON_EXE $PAM_CLIENT --on-pwd-fail"

if [ -f "$PAM_FILE" ]; then
    sed -i "\|faceid_pam_client.py|d" "$PAM_FILE"
    sed -i "s|auth\t\[success=2 default=ignore\]\tpam_unix\.so|auth\t[success=3 default=ignore]\tpam_unix.so|" "$PAM_FILE"
    sed -i "s|auth\t\[success=1 default=ignore\]\tpam_sss\.so|auth\t[success=2 default=ignore]\tpam_sss.so|" "$PAM_FILE"
    sed -i "\|pam_unix\.so|i $PAM_LINE_AUTH" "$PAM_FILE"
    sed -i "\|pam_deny\.so|i $PAM_LINE_FAIL" "$PAM_FILE"
    echo "[1/3] Da tich hop FaceID PAM vao $PAM_FILE (Login Screen, Lock Screen, sudo, polkit)."
fi

mkdir -p /etc/faceid /run/faceid
chmod 777 /run/faceid
ln -sf "$SCRIPT_DIR/config/config.json" /etc/faceid/config.json
ln -sf "$SCRIPT_DIR/config/policy.json" /etc/faceid/policy.json

# 2. Tao va kich hoat systemd service chay ngay tu luc Boot (truoc man hinh Login GDM)
SERVICE_FILE="/etc/systemd/system/faceid-auth.service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=FaceID Hybrid Linux Authentication Daemon
After=network.target systemd-user-sessions.service
Before=display-manager.service gdm.service

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_EXE $DAEMON_SCRIPT
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

echo "[2/3] Dang kich hoat systemd service faceid-auth.service..."
systemctl daemon-reload || true
systemctl enable faceid-auth.service || true
systemctl restart faceid-auth.service || true
sleep 1.5

echo "installed" > /etc/faceid/pam_installed
chmod 644 /etc/faceid/pam_installed
echo "[3/3] Hoan tat cai dat Ubuntu Login & Lock Screen FaceID!"
