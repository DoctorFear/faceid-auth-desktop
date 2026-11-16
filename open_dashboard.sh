#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ -x "$SCRIPT_DIR/venv/bin/python" ]; then
    exec "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/gui/app_gui.py"
else
    exec python3 "$SCRIPT_DIR/gui/app_gui.py"
fi
