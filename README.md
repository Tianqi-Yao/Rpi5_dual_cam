# Rpi5 Dual-Camera 64MP Capture System

A dual-camera capture toolkit for two Arducam 64MP (OV64A40) cameras running on a **Raspberry Pi 5**. Based on the single-camera `../64mp/cam_test/` toolkit, rewritten with pure OpenCV + Picamera2, no rpicam-still.

(中文版见 `README_zh.md`)

---

## Key Difference vs. the Single-Camera `64mp/` Version

`64mp/` was developed for **Rpi4**: Rpi4 has limited RAM, and capturing 64MP directly via Picamera2 (~193MB/frame) causes OOM. Full-resolution captures there must go through an `rpicam-still` subprocess.

**On Rpi5, Picamera2 can capture the full 64MP frame (9248x6944) directly via `capture_array()` without OOM.** As a result, this project:

- Uses **Picamera2 directly + OpenCV display** for everything — no rpicam-still
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
    ├── cam_common.py               # Shared constants/helpers
    ├── preview_focus.py            # Core: interactive dual preview + focus + capture
    ├── batch_capture.py            # Periodic batch capture (AF group + fixed-LP group)
    ├── calibration.py              # Dual-camera focus calibration
    ├── 1_install_service.sh        # Install systemd auto-start
    └── 2_stop_service.sh           # Stop service
```

---

## Module Overview

### `cam_common.py` — Shared module

- `SENSOR_MODES`: `{"full": (9248,6944), "half": (4624,3472), "4k": (3840,2160), "mid": (2312,1736), "1080p": (1920,1080)}`
- `LP_MIN/LP_MAX = 0.0/16.0`, `EV_MIN/EV_MAX = -4.0/4.0`
- `laplacian_sharpness(frame)`: Laplacian-variance sharpness score
- `get_disk_usage()` / `get_cpu_temp()` / `get_memory_usage()` / `log_system_status()`
- `SAVE_DIR_BASE = ~/Desktop/images`

### `preview_focus.py` — Interactive dual preview/focus/capture (core tool)

Single OpenCV window, two preview streams side by side (`np.hstack`). `Tab` switches the "active camera"; all keys act on the active camera (green border highlight).

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
| `t` | One-shot autofocus (locks LP when done) |
| `m` | Toggle save resolution FULL(64MP) ⇄ HALF(16MP) |
| `s` | Save single shot (active camera, current resolution) |
| `S` | Save one shot from BOTH cameras (sequential) |
| `b` | Burst: 5 shots, LP ±0.5 range, step 0.25 |
| `n` | EV bracket: 5 shots, EV ±1.0 range, step 0.5 |
| `f` | Print current state |
| `h` | Print help |
| `q` | Quit |

Output: `~/Desktop/images/preview_captures/cam{N}/{ts}_lp{LP}_ev{EV}_cam{N}.jpg`

### `batch_capture.py` — Periodic batch capture

Every `INTERVAL_SECONDS` (default 1800 = 30 min), cam0 then cam1 **sequentially**, two groups each:

- **AF group**: autofocus → 5 shots centered on best_lp ± 2*STEP (default ±0.4)
- **Fixed-LP group**: 5 shots centered on `FIXED_LP=5.0`

Uses `Picamera2 still configuration` + `capture_array()` + `cv2.imwrite()` throughout — no rpicam-still.

Output: `~/Desktop/images/auto_focus/cam{N}/`, `~/Desktop/images/fixed_focus/cam{N}/`
Log: `~/Desktop/images/batch_log.txt`

### `calibration.py` — Dual-camera focus calibration

```bash
python3 calibration.py [--mode quick|normal|full] [--step 0.5] [--no-fine]
```

Two-phase scan, cam0 then cam1 sequentially:
1. **Coarse**: LensPosition 0.0 to 16.0, step `--step` (default 0.5)
2. **Fine**: best coarse LP ± 1.0, step 0.1 (unless `--no-fine`)

Each step is scored with Laplacian variance; produces a sharpness curve plot and text report per camera.

Output: `~/Desktop/images/calibration_{ts}/cam{N}/{coarse,fine}/` + `report.txt` + `*_curve.png`, plus `summary.txt`.

Scan resolutions and settle times:

| `--mode` | Resolution | Settle |
|---|---|---|
| `quick` | 2312x1736 | 1.0s |
| `normal` (default) | 4624x3472 | 2.0s |
| `full` | 9248x6944 | 5.0s |

---

## Deployment (systemd)

| Script | Purpose |
|---|---|
| `1_install_service.sh` | Install + start systemd `dualcam64.service` (`batch_capture.py`, `Restart=always`) |
| `2_stop_service.sh` | Stop and disable the service |

```bash
sudo systemctl status dualcam64.service     # check status
sudo journalctl -u dualcam64.service -f     # live logs
```

---

## FAQ

**Which CSI port does each camera index correspond to?**
Run `rpicam-hello --list-cameras`; `Picamera2(0)`/`Picamera2(1)` follow the same order as listed there.

**Is direct 64MP capture via Picamera2 really OOM-safe?**
Verified working on Rpi5 (especially 8GB). All 64MP-related scripts here still keep "only one camera in full-res config at a time" as an extra safety margin.

**What's the LensPosition range?**
0.0 = infinity, higher = closer; hardware max is typically around 16 (capped at `LP_MAX=16.0`).

**How do I find the best focus value for each camera?**
Run `calibration.py` first, check `summary.txt`, then set the recommended LP as the sweep center in `batch_capture.py` (`FIXED_LP`) or the initial value in `preview_focus.py` (`INIT_LP`).
