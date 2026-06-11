#!/bin/bash
# Install and start dualcam64.service (systemd, auto-start on boot).
# Runs dual_cam_run.py in the background, restarts on failure.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_USER="${USER:-paalab}"

echo "Creating /etc/systemd/system/dualcam64.service ..."
cat <<EOF | sudo tee /etc/systemd/system/dualcam64.service > /dev/null
[Unit]
Description=Dual Arducam 64MP autofocus capture
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/dual_cam_run.py
Restart=always
RestartSec=5
Environment=QT_QPA_PLATFORM=xcb

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable dualcam64.service
sudo systemctl start dualcam64.service

echo "Done. Check status with:"
echo "  sudo systemctl status dualcam64.service"
echo "  sudo journalctl -u dualcam64.service -f"
