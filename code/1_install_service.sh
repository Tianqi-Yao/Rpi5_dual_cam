#!/bin/bash
# 安装并启动 systemd 服务 dualcam64.service（开机自启 batch_capture.py）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="dualcam64"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Dual 64MP Camera Batch Capture
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/python3 ${SCRIPT_DIR}/batch_capture.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
sudo systemctl start "${SERVICE_NAME}.service"
echo "服务已启动，状态："
sudo systemctl status "${SERVICE_NAME}.service" --no-pager
