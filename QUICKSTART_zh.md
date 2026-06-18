# 快速上手

详细说明见 `README_zh.md`，本文件只列操作步骤。所有命令在 `code/` 目录下执行。

## 0. 安装依赖（Rpi5, 第一次使用）

```bash
sudo apt update
sudo apt install -y python3-picamera2 rpicam-apps
sudo apt install python3-matplotlib python3-opencv
```

## 1. 确认两个摄像头都被识别 + 编号对应

```bash
rpicam-hello --list-cameras
```

记下列表顺序：第一个是 `Picamera2(0)`/`cam0`，第二个是 `Picamera2(1)`/`cam1`。

## 2. 标定每个摄像头的最佳对焦值

```bash
python3 calibration.py --mode normal
```

完成后查看 `~/Desktop/images/calibration_*/summary.txt`，记录两个摄像头各自的建议 LP 值。

## 3. 交互式预览/对焦/拍照

```bash
python3 preview_focus.py
```

核心按键：
- `Tab` 切换操作的相机（cam0/cam1）
- `=`/`-`、`]`/`[`、`.`/`,` 调焦（细/中/粗）
- `t` 一键自动对焦
- `s` 保存当前相机一张，`S` 双摄各保存一张
- `m` 切换 64MP/16MP 保存分辨率
- `h` 查看完整帮助，`q` 退出

## 4. 部署自动采集

**开机自启（systemd）**：
```bash
bash 1_install_service.sh       # 运行 batch_capture.py，注册 dualcam64.service
sudo systemctl status dualcam64.service
sudo journalctl -u dualcam64.service -f
```

**停止**：
```bash
bash 2_stop_service.sh
```

## 输出目录速览

```
~/Desktop/images/
├── preview_captures/cam{0,1}/   # preview_focus.py
├── calibration_*/cam{0,1}/      # calibration.py
├── auto_focus/cam{0,1}/         # batch_capture.py AF 组
├── fixed_focus/cam{0,1}/        # batch_capture.py 固定 LP 组
└── batch_log.txt
```
