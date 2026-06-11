# Rpi5 Dual-Camera 64MP Capture System

A dual-camera capture toolkit for two Arducam 64MP (OV64A40) cameras running on a **Raspberry Pi 5**. This is the dual-camera port of the single-camera toolkit in `../64mp/cam_test/`.

(中文版见 `README_zh.md`)

---

## Key Difference vs. the Single-Camera `64mp/` Version

`64mp/` was developed for **Rpi4**: Rpi4 has limited RAM, and capturing 64MP directly via Picamera2 (~193MB/frame) causes OOM. Full-resolution captures there must go through an `rpicam-still` subprocess (see `../64mp/test/README.md`).

**On Rpi5, Picamera2 can capture the full 64MP frame (9248x6944) directly via `capture_array()` without OOM.** As a result, this project:

- Uses **Picamera2 directly** for everything (preview, focus control, 64MP saves) by default — much simpler than `64mp/`
- Keeps `rpicam-still` subprocess as an **optional backend** for image-quality comparison (`--backend rpicam-still`, or press `v` in the preview tool)
- Processes the two cameras **sequentially** in any batch/automation script that does full-resolution captures, to keep peak memory bounded

---

## Hardware Requirements

| Hardware | Notes |
|---|---|
| 2x Arducam 64MP (OV64A40) | Connected to Rpi5 CAM0 / CAM1 ports |
| Raspberry Pi 5 (8GB recommended) | Needs `rpicam-apps` / `python3-picamera2` |

**Check camera index mapping** (`Picamera2(0)`/`Picamera2(1)` vs. physical CSI port):

```bash
rpicam-hello --list-cameras
# or
python3 -c "from picamera2 import Picamera2; print(Picamera2.global_camera_info())"
```

---

## Directory Structure

```
Rpi5_dual_cam/
├── CLAUDE.md                       # Project notes for Claude Code
├── README.md / README_zh.md        # This document
├── QUICKSTART.md / QUICKSTART_zh.md# Quick start guide
├── .gitignore
└── code/
    ├── dual_cam_common.py          # Shared constants/helpers
    ├── dual_cam_preview_focus.py   # Core: interactive dual preview + focus + capture
    ├── dual_cam_capture.py         # CLI single/dual capture
    ├── dual_cam_calibration.py     # Dual-camera focus calibration
    ├── dual_cam_batch_focus_capture.py  # Periodic LP-sweep batch capture
    ├── dual_cam_run.py             # systemd entry point: autofocus + sweep
    ├── requirements.txt            # pip deps (opencv/numpy/matplotlib)
    │
    ├── 1_check_best_focus.sh       # Step 1: interactive preview/focus
    ├── 2_start_tmux_session.sh     # Step 2: run in tmux
    ├── 3_install_start_auto_services.sh # Step 3: install systemd auto-start
    ├── 4_stop_auto_run.sh          # Stop the service
    │
    ├── dual_cam_opencv.py          # [reference] early demo, superseded
    └── dual_cam_picamera2.py       # [reference] early demo, minimal QtGL preview
```

---

## Module Overview

### `dual_cam_common.py` — Shared module

- `SENSOR_MODES`: `{"full": (9248,6944), "half": (4624,3472), "4k": (3840,2160), "mid": (2312,1736), "1080p": (1920,1080)}`
- `RPICAM_MODE_STR`: corresponding `rpicam-still --mode` strings (e.g. `"9248:6944:12:P"`)
- `LP_MIN/LP_MAX = 0.0/16.0`, `EV_MIN/EV_MAX = -4.0/4.0`
- `laplacian_sharpness(frame)`: Laplacian-variance sharpness score
- `get_disk_usage()` / `get_cpu_temp()` / `get_memory_usage()` / `log_system_status()`
- `SAVE_DIR_BASE = ~/Desktop/images`

### `dual_cam_preview_focus.py` — Interactive dual preview/focus/capture (core tool)

Single OpenCV window, two preview streams side by side (`np.hstack`). `Tab` switches the "active camera"; all keys act on the active camera (highlighted with a colored border).

| Key | Action |
|---|---|
| `Tab` | Switch active camera (cam0 ⇄ cam1) |
| `=` / `-` | LensPosition ±0.1 |
| `]` / `[` | LensPosition ±0.5 |
| `.` / `,` | LensPosition ±1.0 |
| `e` / `w` | Exposure (EV) ±0.5 |
| `z` / `x` | Zoom in / out (ScalerCrop, 1x-20x) |
| `i`/`k`/`j`/`l` | Pan up/down/left/right |
| `r` | Reset zoom to 1x, centered |
| `` ` ``, `1`-`0` | ROI presets (full frame / 2x regions / 4x regions) |
| `t` | One-shot autofocus (locks LP when done) |
| `m` | Toggle save resolution FULL(64MP) ⇄ HALF(16MP) |
| `v` | Toggle save backend picamera2 ⇄ rpicam-still |
| `s` | Save single shot (active camera, current resolution) |
| `S` | Save one shot from BOTH cameras (sequential) |
| `b` | Burst: 5 shots, LP step ±0.25 |
| `n` | EV bracket: 5 shots, EV -1.0 to +1.0 |
| `f` | Print current state |
| `h` | Print help |
| `q` | Quit |

Output: `~/Desktop/images/preview_captures/{ts}_{64mp|16mp}_lp{LP}_ev{EV}_cam{N}.jpg`

### `dual_cam_capture.py` — CLI capture

```bash
python3 dual_cam_capture.py [--camera 0|1|both] [--mode full|half|4k|mid|1080p]
                             [--lens-position F] [--af] [--af-time MS]
                             [--ev F] [--sharpness F]
                             [--backend picamera2|rpicam-still] [-o PATH]
```

| Argument | Default | Description |
|---|---|---|
| `--camera` | `both` | Which camera; `both` processes them sequentially |
| `--mode` | `full` | Resolution, `full` = 64MP |
| `--lens-position` | none | Manual focus; if unset and not `--af`, defaults to 0.0 (infinity) |
| `--af` | off | Autofocus (overrides `--lens-position`) |
| `--af-time` | `5000` | Autofocus wait time (ms) |
| `--ev` | `0.0` | Exposure compensation |
| `--sharpness` | `1.0` | Sharpness |
| `--backend` | `picamera2` | Capture backend |
| `-o` | auto | Output path; with `both`, `_cam{N}` is appended |

Auto filename format: `{ts}_{mode}_{af|lp%.2f}{ev_tag}_cam{N}.jpg`, output dir `~/Desktop/images/captures/`

### `dual_cam_calibration.py` — Dual-camera focus calibration

```bash
python3 dual_cam_calibration.py [--camera 0|1|both] [--step 0.5] [--mode quick|normal|full] [--no-fine]
```

Two-phase scan, per camera:
1. **Coarse**: LensPosition 0.0 to 16.0, step `--step` (default 0.5)
2. **Fine**: best coarse LP ± 1.0, step 0.1 (unless `--no-fine`)

Each step is scored with Laplacian variance; produces a sharpness curve plot and text report per camera.

Output: `~/Desktop/images/calibration_{ts}/cam{N}/{coarse,fine}/` + `report.txt` + `*_curve.png`, plus a `summary.txt` giving the recommended `--lens-position` for `dual_cam_capture.py --camera N`.

Scan resolutions and settle times:

| `--mode` | Resolution | Settle |
|---|---|---|
| `quick` | 2312x1736 | 1.0s |
| `normal` (default) | 4624x3472 | 2.0s |
| `full` | 9248x6944 | 5.0s |

### `dual_cam_batch_focus_capture.py` — Periodic LP-sweep batch capture

Every `INTERVAL_SECONDS` (default 1800 = 30 min), sweeps the full LP range for cam0 then cam1 **sequentially**:

- `CAPTURE_MODE = "full"` (64MP), `LENS_START/END/STEP = 1.0/16.0/0.5` (19 positions)
- `EV_LIST = [0.0]` (add more values for exposure bracketing)

Output: `~/Desktop/images/manual_focus/cam{N}/{ts}_{mode}_lp{LP}{ev_tag}_cam{N}.jpg`
Log: `~/Desktop/images/batch_log.txt`

### `dual_cam_run.py` — systemd entry point (autofocus pipeline)

`main_loop(interval_minutes=30)`. For each cycle, cam0 then cam1:

1. **Stage 1**: `AfMode=2` autofocus (6s wait) → sweep best position ±0.5, step 0.1, 11 shots at half resolution (16MP)
2. **Stage 2**: sweep stage-1 best position ±0.2, step 0.2, 5 shots at full resolution (64MP)

Output: `~/Desktop/images/autofocus_picamera2/cam{N}/`, `~/Desktop/images/manualfocus_full/cam{N}/`
Log: `~/Desktop/images/autofocus_log.txt`

---

## Deployment (systemd / tmux)

| Script | Purpose |
|---|---|
| `1_check_best_focus.sh` | Run `dual_cam_preview_focus.py` for interactive focus |
| `2_start_tmux_session.sh` | tmux session `dualcam64`, runs `dual_cam_batch_focus_capture.py` |
| `3_install_start_auto_services.sh` | Install + start systemd `dualcam64.service` (`dual_cam_run.py`, `Restart=always`) |
| `4_stop_auto_run.sh` | `sudo systemctl stop dualcam64.service` |

```bash
sudo systemctl status dualcam64.service     # check status
sudo journalctl -u dualcam64.service -f     # live logs
tmux attach -t dualcam64                    # attach to tmux session
```

---

## FAQ

**Which CSI port does each camera index correspond to?**
Run `rpicam-hello --list-cameras`; `Picamera2(0)`/`Picamera2(1)` follow the same order as listed there.

**Is direct 64MP capture via Picamera2 really OOM-safe?**
Verified working on Rpi5 (especially 8GB). All 64MP-related scripts here still keep "only one camera in full-res config at a time" as an extra safety margin. If you do hit OOM, fall back to `--backend rpicam-still`.

**What's the LensPosition range?**
0.0 = infinity, higher = closer; hardware max is typically around 16. Scripts read the actual range from `cam.camera_controls["LensPosition"]` (capped at 16.0).

**How do I find the best focus value for each camera?**
Run `dual_cam_calibration.py` first, then use the reported `--lens-position` value with `dual_cam_capture.py`, or hardcode it as the sweep center in `dual_cam_run.py` / `dual_cam_batch_focus_capture.py`.

**Are `dual_cam_opencv.py` / `dual_cam_picamera2.py` still usable?**
They run, but are early minimal demos (no focus control, no 64MP-direct optimizations). Use `dual_cam_preview_focus.py` instead.
