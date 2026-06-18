# Rpi5 双摄 64MP 采集系统

基于两颗 Arducam 64MP（OV64A40）摄像头的双摄采集工具集，运行在 **Raspberry Pi 5** 上。以 `../64mp/cam_test/` 单摄工具集为蓝本，改为纯 OpenCV + Picamera2 实现，无 rpicam-still。

---

## 与单摄 `64mp/` 版本的关键差异

`64mp/` 是为 **Rpi4** 开发的：Rpi4 内存有限，Picamera2 直接拍 64MP（每帧约 193MB）会 OOM，所以全分辨率拍照必须绕道 `rpicam-still` 子进程。

**Rpi5 上 Picamera2 可以直接 `capture_array()` 拿到完整 64MP（9248x6944）而不会 OOM**。因此本项目：

- 全部使用 **Picamera2 直拍 + OpenCV 显示**，无 rpicam-still
- 涉及全分辨率的批量/自动化脚本对两个摄像头**串行**处理，避免同时持有两份 ~193MB 缓冲

---

## 硬件要求

| 硬件 | 说明 |
|---|---|
| Arducam 64MP (OV64A40) ×2 | 分别接到 Rpi5 的 CAM0 / CAM1 接口 |
| Raspberry Pi 5（建议 8GB） | 需安装 `rpicam-apps` / `python3-picamera2` |

**确认相机编号**（`Picamera2(0)`/`Picamera2(1)` 与物理CSI口的对应关系）：

```bash
rpicam-hello --list-cameras
# 或
python3 -c "from picamera2 import Picamera2; print(Picamera2.global_camera_info())"
```

---

## 目录结构

```
Rpi5_dual_cam/
├── CLAUDE.md                       # 给 Claude Code 的项目说明
├── README.md / README_zh.md        # 本文档
├── QUICKSTART.md / QUICKSTART_zh.md# 快速上手
├── .gitignore
└── code/
    ├── cam_common.py               # 公共常量/工具函数
    ├── preview_focus.py            # 核心：交互式双摄预览+对焦+拍照
    ├── batch_capture.py            # 定时批量采集（AF组+固定LP组）
    ├── calibration.py              # 双摄对焦标定
    ├── 1_install_service.sh        # 安装systemd开机自启
    └── 2_stop_service.sh           # 停止服务
```

---

## 功能模块说明

### `cam_common.py` — 公共模块

- `SENSOR_MODES`：`{"full": (9248,6944), "half": (4624,3472), "4k": (3840,2160), "mid": (2312,1736), "1080p": (1920,1080)}`
- `LP_MIN/LP_MAX = 0.0/16.0`，`EV_MIN/EV_MAX = -4.0/4.0`
- `laplacian_sharpness(frame)`：Laplacian 方差清晰度评分
- `get_disk_usage()` / `get_cpu_temp()` / `get_memory_usage()` / `log_system_status()`
- `SAVE_DIR_BASE = ~/Desktop/images`

### `preview_focus.py` — 交互式双摄预览/对焦/拍照（核心）

OpenCV 单窗口，两路预览左右拼接（`np.hstack`）。`Tab` 切换"当前操作的相机"，所有按键作用于激活相机（绿色边框高亮）。

| 按键 | 功能 |
|---|---|
| `Tab` | 切换激活相机 (cam0 ⇄ cam1) |
| `=` / `-` | LP（对焦）±0.1 |
| `]` / `[` | LP ±0.5 |
| `.` / `,` | LP ±1.0 |
| `e` / `w` | EV（曝光补偿）±0.5 |
| `z` / `x` | 放大 / 缩小（ScalerCrop，1x~20x） |
| `i`/`k`/`j`/`l` | 上/下/左/右 平移 |
| `r` | 重置缩放为1x、居中 |
| `t` | 一键自动对焦（锁定后切回手动） |
| `m` | 切换保存分辨率 FULL(64MP) ⇄ HALF(16MP) |
| `s` | 保存单张（激活相机，当前分辨率） |
| `S` | 双摄各保存一张（串行执行） |
| `b` | 连拍5张，LP ±0.5 范围，步进 0.25 |
| `n` | 曝光包围5张，EV ±1.0 范围，步进 0.5 |
| `f` | 打印当前状态 |
| `h` | 打印帮助 |
| `q` | 退出 |

输出：`~/Desktop/images/preview_captures/cam{N}/{ts}_lp{LP}_ev{EV}_cam{N}.jpg`

### `batch_capture.py` — 定时批量采集

每 `INTERVAL_SECONDS`（默认1800秒/30分钟），对 cam0、cam1 **依次**各执行两组拍摄：

- **自动对焦组**：AF 后以 best_lp 为中心，±2*STEP（默认±0.4）共 5 张
- **固定焦距组**：以 `FIXED_LP=5.0` 为中心，5 张

使用 `Picamera2 still configuration` + `capture_array()` + `cv2.imwrite()`，全程无 rpicam-still。

输出：`~/Desktop/images/auto_focus/cam{N}/`、`~/Desktop/images/fixed_focus/cam{N}/`
日志：`~/Desktop/images/batch_log.txt`

### `calibration.py` — 双摄对焦标定

```bash
python3 calibration.py [--mode quick|normal|full] [--step 0.5] [--no-fine]
```

两阶段扫描（cam0、cam1 串行）：
1. **粗扫**：LP 从 0.0 到 16.0，步长 `--step`（默认 0.5）
2. **精扫**：粗扫最佳值 ±1.0，步长 0.1（除非 `--no-fine`）

每步用 Laplacian 方差评分，输出清晰度曲线图和文字报告。

输出：`~/Desktop/images/calibration_{ts}/cam{N}/{coarse,fine}/` + `report.txt` + `*_curve.png`，外加汇总 `summary.txt`。

扫描分辨率与建议 settle 时间：

| `--mode` | 分辨率 | settle |
|---|---|---|
| `quick` | 2312x1736 | 1.0s |
| `normal`（默认） | 4624x3472 | 2.0s |
| `full` | 9248x6944 | 5.0s |

---

## 部署（systemd）

| 脚本 | 作用 |
|---|---|
| `1_install_service.sh` | 安装并启动 systemd `dualcam64.service`（运行 `batch_capture.py`，`Restart=always`） |
| `2_stop_service.sh` | 停止并禁用服务 |

```bash
sudo systemctl status dualcam64.service     # 查看状态
sudo journalctl -u dualcam64.service -f     # 实时日志
```

---

## 常见问题

**两个摄像头分别对应哪个CSI口？**
用 `rpicam-hello --list-cameras` 确认，`Picamera2(0)`/`Picamera2(1)` 的索引顺序与该命令列出的顺序一致。

**Picamera2直拍64MP真的不会OOM吗？**
在 Rpi5（尤其8GB）上已验证可行；本项目所有64MP相关脚本仍坚持"一次只对一个摄像头开启全分辨率配置"，进一步降低风险。

**LensPosition范围是多少？**
0.0=无穷远，数值越大越近，硬件上限通常在16左右（封顶 `LP_MAX=16.0`）。

**如何确定每个摄像头的最佳对焦值？**
先运行 `calibration.py`，查看 `summary.txt`，把得到的 LP 值写入 `batch_capture.py` 的 `FIXED_LP` 或作为 `preview_focus.py` 启动时的初始值。
