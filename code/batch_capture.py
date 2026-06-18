"""
定时批量采集：每 INTERVAL_SECONDS 秒对双摄各执行一次
  - 自动对焦组：AF 后以 best_lp 为中心，±2*STEP 共 5 张
  - 固定焦距组：以 FIXED_LP 为中心，±2*STEP 共 5 张
双摄串行处理（cam0 close 后再开 cam1），避免同时持有双份大缓冲。
"""

import os
import time
import cv2
from picamera2 import Picamera2

from cam_common import (
    SENSOR_MODES, LP_MIN, LP_MAX, SAVE_DIR_BASE,
    clamp, timestamp, log_system_status,
)

INTERVAL_SECONDS = 1800   # 30 分钟
FIXED_LP = 5.0
STEP = 0.2
OFFSETS = [-2, -1, 0, 1, 2]   # 以中心 LP 为基准的偏移倍数
CAPTURE_MODE = "full"          # 9248x6944
AF_MAX_POLLS = 80              # 最多等 8 秒

AUTO_DIR = os.path.join(SAVE_DIR_BASE, "auto_focus")
FIXED_DIR = os.path.join(SAVE_DIR_BASE, "fixed_focus")
LOG_FILE = os.path.join(SAVE_DIR_BASE, "batch_log.txt")


def _log(msg):
    line = f"[{timestamp()}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def autofocus_once(cam_idx):
    cam = Picamera2(cam_idx)
    cfg = cam.create_preview_configuration(
        main={"size": SENSOR_MODES["1080p"], "format": "BGR888"},
        buffer_count=2,
    )
    cam.configure(cfg)
    cam.start()
    cam.set_controls({"AfMode": 1, "AfTrigger": 0})
    lp = None
    for _ in range(AF_MAX_POLLS):
        meta = cam.capture_metadata()
        if meta.get("AfState") == 2:
            lp = meta.get("LensPosition")
            break
        time.sleep(0.1)
    cam.stop()
    cam.close()
    return lp


def capture_group(cam_idx, center_lp, label, out_dir):
    cam_dir = os.path.join(out_dir, f"cam{cam_idx}")
    os.makedirs(cam_dir, exist_ok=True)
    size = SENSOR_MODES[CAPTURE_MODE]

    cam = Picamera2(cam_idx)
    cfg = cam.create_still_configuration(
        main={"size": size, "format": "BGR888"},
        buffer_count=1,
    )
    cam.configure(cfg)
    cam.start()

    ts = timestamp()
    for off in OFFSETS:
        lp = clamp(center_lp + off * STEP, LP_MIN, LP_MAX)
        cam.set_controls({"AfMode": 0, "LensPosition": lp})
        time.sleep(0.5)
        frame = cam.capture_array()
        fname = f"{ts}_{label}_lp{lp:.2f}_cam{cam_idx}.jpg"
        path = os.path.join(cam_dir, fname)
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        _log(f"cam{cam_idx} {label} LP={lp:.2f} → {path}")

    cam.stop()
    cam.close()


def run_one_cycle():
    _log("=== 新采集周期开始 ===")
    log_system_status(_log)

    for cam_idx in range(2):
        # 自动对焦组
        _log(f"cam{cam_idx} 执行自动对焦…")
        best_lp = autofocus_once(cam_idx)
        if best_lp is None:
            _log(f"cam{cam_idx} AF 失败，跳过自动对焦组")
        else:
            _log(f"cam{cam_idx} AF 完成 LP={best_lp:.3f}")
            capture_group(cam_idx, best_lp, "auto", AUTO_DIR)

        # 固定焦距组
        capture_group(cam_idx, FIXED_LP, "fixed", FIXED_DIR)

    _log("=== 采集周期完成 ===")


def main():
    os.makedirs(SAVE_DIR_BASE, exist_ok=True)
    _log("batch_capture 启动")
    while True:
        try:
            run_one_cycle()
        except Exception as e:
            _log(f"错误: {e}")
            _log("等待 120s 后重试…")
            time.sleep(120)
            continue
        _log(f"等待 {INTERVAL_SECONDS}s 后进行下次采集…")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
