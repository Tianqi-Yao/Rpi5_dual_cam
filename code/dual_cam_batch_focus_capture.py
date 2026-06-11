#!/usr/bin/env python3
# Timed batch capture with manual focus sweep for two Arducam 64MP (OV64A40) on Rpi5.
#
# Every INTERVAL_SECONDS: for each camera (processed sequentially to keep memory
# bounded), captures one image at each LensPosition in the sweep.
# Uses Picamera2 directly (Rpi5 can capture full 64MP without OOM).
#
# LensPosition: 0.0 = infinity, 16.0 = ~9-10cm closest
#
# Output: ~/Desktop/images/manual_focus/cam{0,1}/
# Log:    ~/Desktop/images/batch_log.txt

import os
import time
import logging
import cv2
from datetime import datetime
from picamera2 import Picamera2

from dual_cam_common import SENSOR_MODES, SAVE_DIR_BASE, log_system_status

# ========== Configuration ==========
CAPTURE_MODE = "full"          # change to "half" for faster captures
INTERVAL_SECONDS = 1800         # 30 minutes between cycles

# LensPosition sweep: 0.0=infinity, 16.0=~9-10cm
LENS_START = 1.0
LENS_END = 16.0
LENS_STEP = 0.5                 # 0.5 gives 19 positions; use 1.0 for 10 positions

# Exposure compensation applied to every capture
EV_LIST = [0.0]                 # add e.g. [-1, 0, 1] to bracket exposures

SETTLE_TIME = 0.5                # seconds to wait after set_controls before capture

MANUAL_DIR = os.path.join(SAVE_DIR_BASE, "manual_focus")
LOG_FILE = os.path.join(SAVE_DIR_BASE, "batch_log.txt")

os.makedirs(MANUAL_DIR, exist_ok=True)

# ========== Logging ==========
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
)


def log(msg):
    print(msg)
    logging.info(msg)


# ========== Capture ==========
def build_lens_positions():
    n = round((LENS_END - LENS_START) / LENS_STEP)
    return [round(LENS_START + i * LENS_STEP, 2) for i in range(n + 1)]


def capture_focus_sweep(idx):
    size = SENSOR_MODES[CAPTURE_MODE]
    positions = build_lens_positions()
    out_dir = os.path.join(MANUAL_DIR, "cam%d" % idx)
    os.makedirs(out_dir, exist_ok=True)

    log("[cam%d] Sweep mode=%s (%dx%d)  positions=%d (%.1f to %.1f, step %.1f)" % (
        idx, CAPTURE_MODE, size[0], size[1], len(positions), positions[0], positions[-1], LENS_STEP))

    cam = Picamera2(idx)
    try:
        cfg = cam.create_still_configuration(main={"size": size, "format": "BGR888"}, buffer_count=1)
        cam.configure(cfg)
        cam.start()
        cam.set_controls({"AfMode": 0})

        count = 0
        for lp in positions:
            for ev in EV_LIST:
                cam.set_controls({"LensPosition": lp, "ExposureValue": ev})
                time.sleep(SETTLE_TIME)
                frame = cam.capture_array()

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                ev_tag = ("_ev%+.1f" % ev) if ev != 0 else ""
                fname = "%s_%s_lp%.2f%s_cam%d.jpg" % (ts, CAPTURE_MODE, lp, ev_tag, idx)
                path = os.path.join(out_dir, fname)
                cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                log("[cam%d] Captured: %s" % (idx, fname))
                count += 1
    finally:
        cam.stop()
        cam.close()

    log("[cam%d] Done. Captured %d images." % (idx, count))


# ========== Main Loop ==========
def main_loop():
    positions = build_lens_positions()
    log("[Start] Dual-camera batch focus capture")
    log("[Config] Mode=%s  LP=%.1f-%.1f step=%.2f  EV=%s  Interval=%ds" % (
        CAPTURE_MODE, LENS_START, LENS_END, LENS_STEP, EV_LIST, INTERVAL_SECONDS))
    log("[Config] Output: %s" % MANUAL_DIR)
    log("[Config] %d positions per camera" % len(positions))

    while True:
        try:
            log("=== New capture cycle ===")
            log_system_status(log)
            for idx in (0, 1):
                capture_focus_sweep(idx)
            log("[Done] Sleeping %ds...\n" % INTERVAL_SECONDS)
            time.sleep(INTERVAL_SECONDS)
        except Exception as e:
            log("[Error] %s" % e)
            time.sleep(120)


if __name__ == "__main__":
    main_loop()
