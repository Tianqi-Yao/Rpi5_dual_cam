# CLAUDE.md

本文件为 Claude Code 在 `Rpi5_dual_cam/` 目录下工作时的项目上下文说明。

## 项目定位

双摄像头版本的 Arducam 64MP (OV64A40) 采集工具集，运行在 **Raspberry Pi 5** 上。
是 `../64mp/cam_test/` 单摄工具集的复刻 + 双摄改造，参见该目录获取原始单摄实现作为参照。

## 关键架构事实（非常重要，影响所有设计决策）

- **Rpi4 上 Picamera2 拍 64MP 会 OOM**（193MB/帧 × buffer_count），所以 `64mp/` 里全分辨率拍照必须绕道 `rpicam-still` 子进程（详见 `../64mp/test/README.md`）。
- **Rpi5 上 Picamera2 可以直接 `capture_array()` 拿到 64MP（9248x6944）而不会 OOM**（用户已验证）。因此本目录下所有脚本默认使用 Picamera2 直拍，架构比 `64mp/` 简单得多。
- `rpicam-still` 子进程仍作为**可选后端**保留（`dual_cam_capture.py --backend rpicam-still`，`dual_cam_preview_focus.py` 里按 `v` 切换），用于画质对比/兼容旧流程。

## 核心约定

- **双摄串行处理**：任何涉及全分辨率（64MP）拍照的批量/自动化脚本，对 cam0、cam1 **顺序**处理（一个 `cam.close()` 后再开下一个），不同时持有两份 ~193MB 缓冲。
- **分辨率常量**：统一定义在 `code/dual_cam_common.py` 的 `SENSOR_MODES`（Picamera2用，元组）和 `RPICAM_MODE_STR`（rpicam-still用，字符串）。`full = (9248, 6944)`，注意历史代码中曾误写成 `9152`，已修正。
- **文件命名**：`{ts}_..._cam{N}.jpg`，`ts = dual_cam_common.timestamp()`（`YYYYMMDD_HHMMSS`）。
- **输出目录**：统一在 `~/Desktop/images/`（`SAVE_DIR_BASE`）下按功能分子目录（`preview_captures/`、`captures/`、`calibration_*/`、`manual_focus/`、`autofocus_picamera2/`、`manualfocus_full/`），每个目录下再按 `cam0/cam1` 细分。
- **LensPosition**：0.0=无穷远，数值越大越近，上限读取 `cam.camera_controls["LensPosition"][1]`（封顶 `LP_MAX=16.0`）。

## 文件清单

| 文件 | 作用 |
|---|---|
| `code/dual_cam_common.py` | 公共常量（SENSOR_MODES等）、清晰度评分、系统状态函数 |
| `code/dual_cam_preview_focus.py` | 核心交互工具：双摄OpenCV单窗口预览+对焦+拍照 |
| `code/dual_cam_capture.py` | 命令行单/双摄拍照 |
| `code/dual_cam_calibration.py` | 双摄两阶段LP对焦标定 |
| `code/dual_cam_batch_focus_capture.py` | 定时批量LP扫描采集（双摄串行） |
| `code/dual_cam_run.py` | systemd主入口：自动对焦+sweep两阶段流程 |
| `code/1_check_best_focus.sh` ~ `4_stop_auto_run.sh` | 部署脚本（tmux/systemd），服务名`dualcam64.service`，session名`dualcam64` |
| `code/dual_cam_opencv.py` / `code/dual_cam_picamera2.py` | **早期demo，仅供参考**，已被 `dual_cam_preview_focus.py` 取代 |

## 常用命令

本地开发机（macOS）无 Picamera2/rpicam-still，**只能做语法检查**：

```bash
cd code
python3 -m py_compile dual_cam_*.py
bash -n *.sh
```

实机（Rpi5）测试顺序见 `QUICKSTART_zh.md`。

## 相机编号确认

双摄场景下 `Picamera2(0)` / `Picamera2(1)` 与物理CSI口（CAM0/CAM1）的对应关系需要现场确认：

```bash
rpicam-hello --list-cameras
# 或在 Python 中：
python3 -c "from picamera2 import Picamera2; print(Picamera2.global_camera_info())"
```
