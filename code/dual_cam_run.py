#!/usr/bin/env python3
# Main automated capture loop for two Arducam 64MP (OV64A40) on Raspberry Pi 5.
# Intended to run as a systemd service (see 3_install_start_auto_services.sh).
#
# On Rpi5, Picamera2 can capture the full 64MP frame directly (no OOM), so the
# whole pipeline runs through Picamera2 (no rpicam-still subprocess needed).
#
# Each cycle, for each camera (processed sequentially to keep memory bounded):
#   Stage 1: Picamera2 autofocus (AfMode=2), then sweep best_pos +/-0.5 step 0.1
#            (11 shots) at half resolution (4624x3472 / 16MP)
#   Stage 2: sweep best_pos +/-0.2 step 0.2 (5 shots) at full resolution
#            (9248x6944 / 64MP)
#
# Output: ~/Desktop/images/autofocus_picamera2/cam{0,1}/
#         ~/Desktop/images/manualfocus_full/cam{0,1}/
# Log:    ~/Desktop/images/autofocus_log.txt

import os
import time
import logging
import cv2
from datetime import datetime
from picamera2 import Picamera2

from dual_cam_common import SENSOR_MODES, SAVE_DIR_BASE, log_system_status

# ========== Paths ==========
AUTOFOCUS_DIR = os.path.join(SAVE_DIR_BASE, "autofocus_picamera2")
MANUAL_FULL_DIR = os.path.join(SAVE_DIR_BASE, "manualfocus_full")
LOG_FILE = os.path.join(SAVE_DIR_BASE, "autofocus_log.txt")

os.makedirs(AUTOFOCUS_DIR, exist_ok=True)
os.makedirs(MANUAL_FULL_DIR, exist_ok=True)

# ========== Logging ==========
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
)


def log(msg):
    print(msg)
    logging.info(msg)


# ========== Stage 1: Picamera2 autofocus + sweep (half resolution) ==========
def run_autofocus_sweep(idx):
    """Autofocus, then sweep best_pos +/-0.5 step 0.1 (11 shots) at half resolution."""
    out_dir = os.path.join(AUTOFOCUS_DIR, "cam%d" % idx)
    os.makedirs(out_dir, exist_ok=True)

    cam = Picamera2(idx)
    try:
        preview_cfg = cam.create_preview_configuration(main={"size": (1920, 1080), "format": "BGR888"})
        cam.configure(preview_cfg)
        cam.start()
        time.sleep(1)

        cam.set_controls({"AfMode": 2})
        time.sleep(6)
        metadata = cam.capture_metadata()
        best_pos = metadata.get("LensPosition", None)
        if best_pos is None:
            log("[cam%d][Picamera2] Autofocus failed." % idx)
            return None

        log("[cam%d][Picamera2] Best Focus: %.2f" % (idx, best_pos))

        capture_cfg = cam.create_still_configuration(
            main={"size": SENSOR_MODES["half"], "format": "BGR888"}, buffer_count=1)
        cam.configure(capture_cfg)
        cam.start()
        cam.set_controls({"AfMode": 0})

        for i in range(-5, 6):
            lp = round(best_pos + 0.1 * i, 2)
            cam.set_controls({"LensPosition": lp})
            time.sleep(0.4)
            frame = cam.capture_array()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = "%s_lp%.2f_cam%d.jpg" % (ts, lp, idx)
            cv2.imwrite(os.path.join(out_dir, fname), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            log("[cam%d][Picamera2] Captured: %s" % (idx, fname))

        return best_pos
    finally:
        cam.stop()
        cam.close()


# ========== Stage 2: full 64MP manual sweep around best position ==========
def run_full_res_sweep(idx, center_pos):
    """Sweep center_pos +/-0.2 step 0.2 (5 shots) at full 64MP resolution."""
    if center_pos is None:
        log("[cam%d][FullRes] Skipped (no autofocus result)" % idx)
        return

    out_dir = os.path.join(MANUAL_FULL_DIR, "cam%d" % idx)
    os.makedirs(out_dir, exist_ok=True)

    cam = Picamera2(idx)
    try:
        cfg = cam.create_still_configuration(
            main={"size": SENSOR_MODES["full"], "format": "BGR888"}, buffer_count=1)
        cam.configure(cfg)
        cam.start()
        cam.set_controls({"AfMode": 0})

        for i in range(-2, 3):
            lp = round(center_pos + 0.2 * i, 2)
            cam.set_controls({"LensPosition": lp})
            time.sleep(0.5)
            frame = cam.capture_array()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = "%s_lp%.2f_cam%d.jpg" % (ts, lp, idx)
            cv2.imwrite(os.path.join(out_dir, fname), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            log("[cam%d][FullRes] Captured: %s" % (idx, fname))
    finally:
        cam.stop()
        cam.close()


# ========== Main Loop ==========
def main_loop(interval_minutes=30):
    while True:
        try:
            log("[Start] New capture cycle")
            log_system_status(log)
            for idx in (0, 1):
                best_focus = run_autofocus_sweep(idx)
                run_full_res_sweep(idx, best_focus)
            log("[Done] Sleeping...\n")
            time.sleep(interval_minutes * 60)
        except Exception as e:
            log("[Error] %s" % e)
            time.sleep(120)


if __name__ == "__main__":
    main_loop()
