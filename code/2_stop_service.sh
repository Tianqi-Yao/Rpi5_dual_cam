#!/bin/bash
# 停止并禁用 dualcam64.service
sudo systemctl stop dualcam64.service
sudo systemctl disable dualcam64.service
echo "服务已停止"
