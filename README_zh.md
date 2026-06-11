# Rpi5 双摄 64MP 采集系统

基于两颗 Arducam 64MP（OV64A40）摄像头的双摄采集工具集，运行在 **Raspberry Pi 5** 上。是 `../64mp/cam_test/` 单摄工具集的双摄复刻版。

---

## 与单摄 `64mp/` 版本的关键差异

`64mp/` 是为 **Rpi4** 开发的：Rpi4 内存有限，Picamera2 直接拍 64MP（每帧约193MB）会 OOM，所以全分辨率拍照必须绕道 `rpicam-still` 子进程（详见 `../64mp/test/README.md`）。

**Rpi5 上 Picamera2 可以直接 `capture_array()` 拿到完整 64MP（9248x6944）而不会 OOM**。因此本项目：

- 默认全部使用 **Picamera2 直拍**（预览、对焦、64MP保存统一一套API），架构比 `64mp/` 简单很多
- `rpicam-still` 子进程仍作为**可选后端**保留，用于画质对比（`--backend rpicam-still` / 预览工具按 `v` 切换）
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
    ├── dual_cam_common.py          # 公共常量/工具函数
    ├── dual_cam_preview_focus.py   # 核心：交互式双摄预览+对焦+拍照
    ├── dual_cam_capture.py         # 命令行单/双摄拍照
    ├── dual_cam_calibration.py     # 双摄对焦标定
    ├── dual_cam_batch_focus_capture.py  # 定时批量LP扫描采集
    ├── dual_cam_run.py             # systemd主入口：自动对焦+sweep
    ├── requirements.txt            # pip依赖（opencv/numpy/matplotlib）
    │
    ├── 1_check_best_focus.sh       # 步骤1：交互预览调焦
    ├── 2_start_tmux_session.sh     # 步骤2：tmux后台运行
    ├── 3_install_start_auto_services.sh # 步骤3：安装systemd开机自启
    ├── 4_stop_auto_run.sh          # 停止服务
    │
    ├── dual_cam_opencv.py          # [参考] 早期demo，已被preview_focus取代
    └── dual_cam_picamera2.py       # [参考] 早期demo，最简QtGL双预览
```

---

## 功能模块说明

### `dual_cam_common.py` — 公共模块

- `SENSOR_MODES`：`{"full": (9248,6944), "half": (4624,3472), "4k": (3840,2160), "mid": (2312,1736), "1080p": (1920,1080)}`
- `RPICAM_MODE_STR`：对应的 `rpicam-still --mode` 字符串（如 `"9248:6944:12:P"`）
- `LP_MIN/LP_MAX = 0.0/16.0`，`EV_MIN/EV_MAX = -4.0/4.0`
- `laplacian_sharpness(frame)`：Laplacian方差清晰度评分
- `get_disk_usage()` / `get_cpu_temp()` / `get_memory_usage()` / `log_system_status()`
- `SAVE_DIR_BASE = ~/Desktop/images`

### `dual_cam_preview_focus.py` — 交互式双摄预览/对焦/拍照（核心）

OpenCV 单窗口，两路预览左右拼接（`np.hstack`）。`Tab` 切换"当前操作的相机"，所有按键作用于激活相机（边框高亮显示）。

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
| `` ` ``、`1`-`0` | ROI预设（全画面 / 2x区域 / 4x区域） |
| `t` | 一键自动对焦（锁定后切回手动） |
| `m` | 切换保存分辨率 FULL(64MP) ⇄ HALF(16MP) |
| `v` | 切换保存后端 picamera2 ⇄ rpicam-still |
| `s` | 保存单张（激活相机，当前分辨率） |
| `S` | 双摄各保存一张（串行执行） |
| `b` | 连拍5张，LP ±0.25 步进 |
| `n` | 曝光包围5张，EV -1.0~+1.0 |
| `f` | 打印当前状态 |
| `h` | 打印帮助 |
| `q` | 退出 |

输出：`~/Desktop/images/preview_captures/{ts}_{64mp|16mp}_lp{LP}_ev{EV}_cam{N}.jpg`

### `dual_cam_capture.py` — 命令行拍照

```bash
python3 dual_cam_capture.py [--camera 0|1|both] [--mode full|half|4k|mid|1080p]
                             [--lens-position F] [--af] [--af-time MS]
                             [--ev F] [--sharpness F]
                             [--backend picamera2|rpicam-still] [-o PATH]
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--camera` | `both` | 选择相机，`both`时两台**顺序**处理 |
| `--mode` | `full` | 分辨率，`full`=64MP |
| `--lens-position` | 无 | 手动对焦值；不指定且未`--af`时为0.0(无穷远) |
| `--af` | 关闭 | 自动对焦（覆盖`--lens-position`） |
| `--af-time` | `5000` | 自动对焦等待时间(ms) |
| `--ev` | `0.0` | 曝光补偿 |
| `--sharpness` | `1.0` | 锐化 |
| `--backend` | `picamera2` | 拍照后端 |
| `-o` | 自动命名 | 输出路径；`both`时自动追加`_cam{N}` |

自动命名格式：`{ts}_{mode}_{af|lp%.2f}{ev_tag}_cam{N}.jpg`，输出目录 `~/Desktop/images/captures/`

### `dual_cam_calibration.py` — 双摄对焦标定

```bash
python3 dual_cam_calibration.py [--camera 0|1|both] [--step 0.5] [--mode quick|normal|full] [--no-fine]
```

两阶段扫描（每相机独立）：
1. **粗扫**：LP从0.0到16.0，步长`--step`（默认0.5）
2. **精扫**：粗扫最佳值 ±1.0，步长0.1（除非 `--no-fine`）

每步用 Laplacian 方差评分，输出每个相机的清晰度曲线图和文字报告。

输出：`~/Desktop/images/calibration_{ts}/cam{N}/{coarse,fine}/` + `report.txt` + `*_curve.png`，外加汇总 `summary.txt`（直接给出可用于 `dual_cam_capture.py --camera N --lens-position X` 的最佳值）。

扫描分辨率与建议settle时间：

| `--mode` | 分辨率 | settle |
|---|---|---|
| `quick` | 2312x1736 | 1.0s |
| `normal`（默认） | 4624x3472 | 2.0s |
| `full` | 9248x6944 | 5.0s |

### `dual_cam_batch_focus_capture.py` — 定时批量LP扫描

每 `INTERVAL_SECONDS`（默认1800秒/30分钟），对 cam0、cam1 **依次**做完整LP扫描：

- `CAPTURE_MODE = "full"`（64MP），`LENS_START/END/STEP = 1.0/16.0/0.5`（19个位置）
- `EV_LIST = [0.0]`（可改为多个值做曝光包围）

输出：`~/Desktop/images/manual_focus/cam{N}/{ts}_{mode}_lp{LP}{ev_tag}_cam{N}.jpg`
日志：`~/Desktop/images/batch_log.txt`

### `dual_cam_run.py` — systemd主入口（自动对焦流程）

`main_loop(interval_minutes=30)`，每轮对 cam0、cam1 依次执行：

1. **阶段1**：`AfMode=2` 自动对焦（等6秒）→ 在最佳位置 ±0.5、步长0.1 拍11张（半分辨率16MP）
2. **阶段2**：以阶段1最佳位置为中心，±0.2、步长0.2 拍5张（全分辨率64MP）

输出：`~/Desktop/images/autofocus_picamera2/cam{N}/`、`~/Desktop/images/manualfocus_full/cam{N}/`
日志：`~/Desktop/images/autofocus_log.txt`

---

## 部署（systemd / tmux）

| 脚本 | 作用 |
|---|---|
| `1_check_best_focus.sh` | 运行 `dual_cam_preview_focus.py` 交互调焦 |
| `2_start_tmux_session.sh` | tmux session `dualcam64`，运行 `dual_cam_batch_focus_capture.py` |
| `3_install_start_auto_services.sh` | 安装并启动 systemd `dualcam64.service`（运行 `dual_cam_run.py`，`Restart=always`） |
| `4_stop_auto_run.sh` | `sudo systemctl stop dualcam64.service` |

```bash
sudo systemctl status dualcam64.service     # 查看状态
sudo journalctl -u dualcam64.service -f     # 实时日志
tmux attach -t dualcam64                    # 附加到tmux会话
```

---

## 常见问题

**两个摄像头分别对应哪个CSI口？**
用 `rpicam-hello --list-cameras` 确认，`Picamera2(0)`/`Picamera2(1)` 的索引顺序与该命令列出的顺序一致。

**Picamera2直拍64MP真的不会OOM吗？**
在Rpi5（尤其8GB）上已验证可行；本项目所有64MP相关脚本仍坚持"一次只对一个摄像头开启全分辨率配置"，进一步降低风险。如果遇到OOM，可改用 `--backend rpicam-still`。

**LensPosition范围是多少？**
0.0=无穷远，数值越大越近，硬件上限通常在16左右（脚本运行时会读取 `cam.camera_controls["LensPosition"]` 实际范围，封顶16.0）。

**如何确定每个摄像头的最佳对焦值？**
先运行 `dual_cam_calibration.py`，再把得到的 `--lens-position` 值用于 `dual_cam_capture.py` 或写入 `dual_cam_run.py`/`dual_cam_batch_focus_capture.py` 的扫描中心。

**`dual_cam_opencv.py` / `dual_cam_picamera2.py` 还能用吗？**
能运行，但只是早期最简demo（无对焦控制、无64MP直拍优化），建议改用 `dual_cam_preview_focus.py`。
