#!/usr/bin/env bash
# Run on the Linux host: sudo bash scripts/setup_server_ocr.sh
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
    echo 'Run this setup with sudo.' >&2
    exit 1
fi
addon=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
service=${SHOP_ODOO_SERVICE:-kmlshop}
ocr_python=${SHOP_PADDLE_PYTHON:-/opt/kmlshop/ocr-venv/bin/python3}
ocr_user=$(systemctl show "$service" -p User --value)
ocr_user=${ocr_user:-root}

apt-get update
apt-get install -y python3-venv tesseract-ocr libgl1 libglib2.0-0
if [[ ! -x $ocr_python ]]; then
    python3 -m venv "$(dirname -- "$(dirname -- "$ocr_python")")"
fi
"$ocr_python" -m pip install -r "$addon/requirements-paddle-server.txt"
install -d -o "$ocr_user" "$addon/.ocr-models"
# Downloaded models and runtime cache belong only to the OCR service account.
chown -R "$ocr_user" "$addon/.ocr-models"
cd "$addon"
sudo -u "$ocr_user" "$ocr_python" "$addon/models/paddle_bill_worker.py" --setup
sudo -u "$ocr_user" "$ocr_python" "$addon/scripts/setup_local_ocr.py"
sudo -u "$ocr_user" "$ocr_python" "$addon/models/paddle_bill_worker.py" --check

install -d "/etc/systemd/system/$service.service.d"
printf '[Service]\nWorkingDirectory=%s\nEnvironment="SHOP_PADDLE_PYTHON=%s"\n' \
    "$addon" "$ocr_python" > "/etc/systemd/system/$service.service.d/ocr.conf"
systemctl daemon-reload
systemctl restart "$service"
echo "OCR setup and inference check passed; $service restarted."
