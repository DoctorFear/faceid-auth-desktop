#!/usr/bin/env bash
# ==============================================================================
# run_pam_simulation.sh - Kich ban kiem thu toan trinh PAM + Fallback tren Ubuntu
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

PYTHON_EXE="$SCRIPT_DIR/venv/bin/python"
if [ ! -x "$PYTHON_EXE" ]; then
    PYTHON_EXE="$(command -v python3)"
fi
TARGET_USER="${USER:-son}"

echo "=============================================================================="
echo "  FACEID HYBRID - MO PHONG XAC THUC LINUX PAM VA CO CHE FALLBACK (UBUNTU)"
echo "=============================================================================="
echo ""
echo "[1] Kiem tra trang thai he thong..."
"$PYTHON_EXE" scripts/faceidctl status || true
echo ""

while true; do
    echo "------------------------------------------------------------------------------"
    echo "Chon che do kiem thu:"
    echo "  [1] Kiem thu xac thuc 'sudo' (Nguong 0.75 + Bat buoc Liveness Chop mat/Cuoi)"
    echo "  [2] Kiem thu xac thuc 'gnome-screensaver' (Nguong 0.65 + Mo sieu toc)"
    echo "  [3] Kiem thu Fallback khi Camera ban / Che camera / Khong dung mat (Exit 1)"
    echo "  [4] Kiem thu Bypass khi Tat FaceID (faceidctl off - Fallback 100%)"
    echo "  [5] Kiem thu Phong thu Fuzz Testing Socket (6 bai test do ben)"
    echo "  [6] Thoat"
    echo "------------------------------------------------------------------------------"
    read -rp "Nhap lua chon [1-6]: " opt

    case "$opt" in
        1)
            echo "[*] Dang khoi dong Daemon ngam..."
            "$PYTHON_EXE" scripts/faceidctl start >/dev/null 2>&1 || true
            echo "[*] Goi yeu cau xac thuc cho dich vu 'sudo' (User: $TARGET_USER)..."
            if "$PYTHON_EXE" scripts/faceid_pam_client.py "$TARGET_USER" sudo; then
                echo "[+] KET QUA: PAM_SUCCESS (Cho phep lenh sudo thuc thi)"
            else
                echo "[-] KET QUA: FALLBACK PASSWORD (Chuyen sang nhap mat khau)"
            fi
            read -rp "Nhan Enter de tiep tuc..." _
            ;;
        2)
            echo "[*] Dang khoi dong Daemon ngam..."
            "$PYTHON_EXE" scripts/faceidctl start >/dev/null 2>&1 || true
            echo "[*] Goi yeu cau xac thuc cho dich vu 'gnome-screensaver' (User: $TARGET_USER)..."
            if "$PYTHON_EXE" scripts/faceid_pam_client.py "$TARGET_USER" gnome-screensaver; then
                echo "[+] KET QUA: PAM_SUCCESS (Mo khoa man hinh thanh cong)"
            else
                echo "[-] KET QUA: FALLBACK PASSWORD (Chuyen sang nhap mat khau)"
            fi
            read -rp "Nhan Enter de tiep tuc..." _
            ;;
        3)
            echo "[*] Dang khoi dong Daemon ngam..."
            "$PYTHON_EXE" scripts/faceidctl start >/dev/null 2>&1 || true
            echo "[CHU Y] Vui long CHE TAY LEN CAMERA hoac KHONG NHIN VAO WEBCAM de test Fallback!"
            if ! "$PYTHON_EXE" scripts/faceid_pam_client.py "$TARGET_USER" sudo; then
                echo "[+] CHUNG MINH THANH CONG: Khi loi hoac khong thay mat, Exit Code luon la 1."
            fi
            read -rp "Nhan Enter de tiep tuc..." _
            ;;
        4)
            echo "[*] Buoc 1: Tat cong tac FaceID..."
            "$PYTHON_EXE" scripts/faceidctl off
            echo "[*] Buoc 2: Chay client kiem thu..."
            "$PYTHON_EXE" scripts/faceid_pam_client.py "$TARGET_USER" sudo || true
            echo "[*] Buoc 3: Bat lai cong tac FaceID ve ban dau..."
            "$PYTHON_EXE" scripts/faceidctl on
            read -rp "Nhan Enter de tiep tuc..." _
            ;;
        5)
            echo "[*] Dang khoi dong Daemon ngam..."
            "$PYTHON_EXE" scripts/faceidctl start >/dev/null 2>&1 || true
            echo "[*] Bat dau Fuzz Testing..."
            "$PYTHON_EXE" eval/fuzz_socket.py
            read -rp "Nhan Enter de tiep tuc..." _
            ;;
        6)
            echo "[*] Hen gap lai!"
            break
            ;;
    esac
done
