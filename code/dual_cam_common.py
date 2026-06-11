#!/usr/bin/env python3
# Shared constants and helpers for the Rpi5 dual-camera (Arducam 64MP OV64A40 x2) toolset.
#
# On Rpi5, Picamera2 can capture the full 64MP frame directly (no OOM), unlike
# Rpi4 where rpicam-still subprocess was required for full-res captures.
# rpicam-still remains available as an optional backend for comparison.

import os
import shutil
import subprocess

import cv2

# -- Sensor / capture resolutions (OV64A40) --
# Spec: full(64MP ~2fps) half(16MP ~7.6fps) 4k(~14.8fps) mid(4MP ~26.7fps) 1080p(~45fps)
SENSOR_MODES = {
    "full":  (9248, 6944),
    "half":  (4624, 3472),
    "4k":    (3840, 2160),
    "mid":   (2312, 1736),
    "1080p": (1920, 1080),
}

# rpicam-still --mode strings for the same resolutions
RPICAM_MODE_STR = {
    "full":  "9248:6944:12:P",
    "half":  "4624:3472:12:P",
    "4k":    "3840:2160:12:P",
    "mid":   "2312:1736:12:P",
    "1080p": "1920:1080:12:P",
}

SENSOR_W, SENSOR_H = SENSOR_MODES["full"]

# -- Focus / exposure ranges --
# LensPosition: 0.0 = infinity, higher = closer (~9-10cm at max)
LP_MIN = 0.0
LP_MAX = 16.0
EV_MIN = -4.0
EV_MAX = 4.0

# -- Output paths --
SAVE_DIR_BASE = os.path.expanduser("~/Desktop/images")


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def timestamp():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def laplacian_sharpness(bgr_frame):
    """Laplacian-variance sharpness score for a BGR frame (higher = sharper)."""
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


# -- System status (used by batch capture / run scripts) --
def get_disk_usage(path=SAVE_DIR_BASE):
    try:
        usage = shutil.disk_usage(path)
        return "%.2fGB free" % (usage.free / (1024 ** 3))
    except Exception:
        return "N/A"


def get_cpu_temp():
    try:
        return subprocess.check_output(["vcgencmd", "measure_temp"]).decode().strip().split("=")[1]
    except Exception:
        return "N/A"


def get_memory_usage():
    try:
        mem = subprocess.check_output("free -m", shell=True).decode().splitlines()[1].split()
        used, total = int(mem[2]), int(mem[1])
        return "%d/%dMB (%.1f%%)" % (used, total, used / total * 100)
    except Exception:
        return "N/A"


def log_system_status(log_fn):
    log_fn("[Sys] Disk=%s  Temp=%s  Mem=%s" % (get_disk_usage(), get_cpu_temp(), get_memory_usage()))
