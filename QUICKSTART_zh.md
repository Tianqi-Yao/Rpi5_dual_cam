# 快速上手

详细说明见 `README_zh.md`，本文件只列操作步骤。所有命令在 `code/` 目录下执行。

## 0. 安装依赖（Rpi5, 第一次使用）

```bash
sudo apt update
sudo apt install -y python3-picamera2 rpicam-apps tmux
pip install -r requirements.txt   # opencv-python numpy matplotlib
```

## 1. 确认两个摄像头都被识别 + 编号对应

```bash
rpicam-hello --list-cameras
```

记下列表顺序：第一个是 `Picamera2(0)`/`cam0`，第二个是 `Picamera2(1)`/`cam1`。

## 2. 快速验证双摄都能拍照（16MP，速度快）

```bash
python3 dual_cam_capture.py --camera both --mode half
```

输出在 `~/Desktop/images/captures/`，应得到 `..._cam0.jpg` 和 `..._cam1.jpg` 两张图。

## 3. 验证64MP直拍（确认Rpi5不OOM）

```bash
python3 dual_cam_capture.py --camera both --mode full
```

观察是否报错/被OOM killer杀掉（`dmesg | tail` 检查）。如有问题可改用 `--backend rpicam-still` 对比。

## 4. 标定每个摄像头的最佳对焦值

```bash
python3 dual_cam_calibration.py --camera both --mode normal
```

完成后查看 `~/Desktop/images/calibration_*/summary.txt`，记录两个摄像头各自的最佳 `--lens-position`。

## 5. 交互式预览/对焦/拍照

```bash
bash 1_check_best_focus.sh
# 等价于: python3 dual_cam_preview_focus.py
```

核心按键：
- `Tab` 切换操作的相机（cam0/cam1）
- `=`/`-`、`]`/`[`、`.`/`,` 调焦（细/中/粗）
- `t` 一键自动对焦
- `s` 保存当前相机一张，`S` 双摄各保存一张
- `m` 切换64MP/16MP保存分辨率，`v` 切换picamera2/rpicam-still后端
- `h` 查看完整帮助，`q` 退出

## 6. 部署自动采集

**临时运行（tmux）**：
```bash
bash 2_start_tmux_session.sh        # 运行 dual_cam_batch_focus_capture.py
tmux attach -t dualcam64            # 查看
```

**开机自启（systemd）**：
```bash
bash 3_install_start_auto_services.sh   # 运行 dual_cam_run.py，注册 dualcam64.service
sudo systemctl status dualcam64.service
sudo journalctl -u dualcam64.service -f
```

**停止**：
```bash
bash 4_stop_auto_run.sh
```

## 输出目录速览

```
~/Desktop/images/
├── captures/              # dual_cam_capture.py
├── preview_captures/      # dual_cam_preview_focus.py
├── calibration_*/         # dual_cam_calibration.py
├── manual_focus/cam{0,1}/ # dual_cam_batch_focus_capture.py
├── autofocus_picamera2/cam{0,1}/  # dual_cam_run.py 阶段1
├── manualfocus_full/cam{0,1}/     # dual_cam_run.py 阶段2
├── batch_log.txt
└── autofocus_log.txt
```
