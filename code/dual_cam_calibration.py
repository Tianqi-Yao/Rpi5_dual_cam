#!/usr/bin/env python3
# Focus calibration tool for two Arducam 64MP (OV64A40) on Raspberry Pi 5.
#
# Two-phase scan per camera:
#   Phase 1 (coarse): scans SCAN_START to SCAN_END with --step (default 0.5)
#   Phase 2 (fine):   scans +-FINE_RANGE around best position with step 0.1
#
# Measures Laplacian sharpness at each position, saves images,
# plots the sharpness curve, and reports the best LensPosition per camera.
#
# LensPosition: 0.0 = infinity, 16.0 = ~9-10cm closest
# Scan modes (resolution used during scan):
#   quick  2312x1736 ~26.7fps  fast, less detail
#   normal 4624x3472 ~7.6fps   recommended
#   full   9248x6944 ~2fps     slowest, maximum accuracy
#
# Output: ~/Desktop/images/calibration_YYYYMMDD_HHMMSS/cam{N}/{coarse,fine}/
#         + per-camera report.txt / curves + summary.txt
#
# Usage:
#   python3 dual_cam_calibration.py
#   python3 dual_cam_calibration.py --camera 0 --step 1.0 --mode quick
#   python3 dual_cam_calibration.py --camera both --step 0.5 --mode normal --no-fine

import os
import time
import argparse
import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from picamera2 import Picamera2

from dual_cam_common import laplacian_sharpness, timestamp, SAVE_DIR_BASE

SCAN_MODES = {
    "quick":  (2312, 1736),
    "normal": (4624, 3472),
    "full":   (9248, 6944),
}

SETTLE_TIMES = {
    "quick":  1.0,
    "normal": 2.0,
    "full":   5.0,
}

SCAN_START = 0.0
SCAN_END = 16.0
FINE_RANGE = 1.0   # scan best_lp +/- FINE_RANGE in phase 2
FINE_STEP = 0.1


def parse_args():
    parser = argparse.ArgumentParser(description="Focus calibration - dual Arducam 64MP")
    parser.add_argument("--camera", choices=["0", "1", "both"], default="both",
                         help="Which camera to calibrate (default: both)")
    parser.add_argument("--step", type=float, default=0.5,
                         help="Coarse scan step (default: 0.5)")
    parser.add_argument("--mode", choices=list(SCAN_MODES.keys()), default="normal",
                         help="Scan resolution: quick/normal/full (default: normal)")
    parser.add_argument("--no-fine", action="store_true",
                         help="Skip phase-2 fine scan around best position")
    return parser.parse_args()


def scan_range(picam2, positions, settle, out_dir, label):
    os.makedirs(out_dir, exist_ok=True)
    scores = []
    print("[%s] %d positions: %.2f -> %.2f" % (label, len(positions), positions[0], positions[-1]))

    for lp in positions:
        picam2.set_controls({"LensPosition": lp})
        time.sleep(settle)
        frame = picam2.capture_array()
        score = laplacian_sharpness(frame)
        scores.append(score)
        fname = "lp%.2f_s%.0f.jpg" % (lp, score)
        cv2.imwrite(os.path.join(out_dir, fname), frame)
        print("  LP=%.2f  sharpness=%.1f" % (lp, score))

    return scores


def plot_curve(positions, scores, best_lp, best_score, path, title):
    plt.figure(figsize=(10, 5))
    plt.plot(positions, scores, "b-o", markersize=4, linewidth=1.5)
    plt.axvline(best_lp, color="r", linestyle="--",
                label="Best LP=%.2f  score=%.0f" % (best_lp, best_score))
    plt.xlabel("LensPosition  (0.0=infinity, 16.0=~9-10cm)")
    plt.ylabel("Sharpness (Laplacian variance)")
    plt.title(title)
    plt.legend()
    plt.grid(True, alpha=0.4)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def calibrate_camera(idx, args, out_root):
    width, height = SCAN_MODES[args.mode]
    settle = SETTLE_TIMES[args.mode]
    cam_dir = os.path.join(out_root, "cam%d" % idx)
    os.makedirs(cam_dir, exist_ok=True)

    print("\n=== Camera %d ===" % idx)
    print("Mode=%s (%dx%d)  step=%.2f  settle=%.1fs" % (args.mode, width, height, args.step, settle))

    picam2 = Picamera2(idx)
    config = picam2.create_preview_configuration(
        main={"size": (width, height), "format": "BGR888"}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)
    picam2.set_controls({"AfMode": 0, "LensPosition": SCAN_START})
    time.sleep(settle)

    # Phase 1: coarse scan
    n = round((SCAN_END - SCAN_START) / args.step)
    coarse_pos = [round(SCAN_START + i * args.step, 2) for i in range(n + 1)]
    coarse_scores = scan_range(picam2, coarse_pos, settle,
                                os.path.join(cam_dir, "coarse"), "cam%d Phase1-Coarse" % idx)

    best_idx = int(np.argmax(coarse_scores))
    best_lp_coarse = coarse_pos[best_idx]
    print("Coarse best: LP=%.2f  sharpness=%.1f" % (best_lp_coarse, coarse_scores[best_idx]))

    plot_curve(coarse_pos, coarse_scores, best_lp_coarse, coarse_scores[best_idx],
               os.path.join(cam_dir, "coarse_curve.png"), "Camera %d - Coarse Scan" % idx)

    # Phase 2: fine scan around best position
    fine_pos = []
    fine_scores = []
    best_lp_final = best_lp_coarse
    best_score_final = coarse_scores[best_idx]

    if not args.no_fine:
        fine_start = max(SCAN_START, round(best_lp_coarse - FINE_RANGE, 2))
        fine_end = min(SCAN_END, round(best_lp_coarse + FINE_RANGE, 2))
        n_fine = round((fine_end - fine_start) / FINE_STEP)
        fine_pos = [round(fine_start + i * FINE_STEP, 2) for i in range(n_fine + 1)]
        fine_scores = scan_range(picam2, fine_pos, settle,
                                  os.path.join(cam_dir, "fine"), "cam%d Phase2-Fine" % idx)

        fine_best_idx = int(np.argmax(fine_scores))
        best_lp_final = fine_pos[fine_best_idx]
        best_score_final = fine_scores[fine_best_idx]
        print("Fine best: LP=%.2f  sharpness=%.1f" % (best_lp_final, best_score_final))

        plot_curve(fine_pos, fine_scores, best_lp_final, best_score_final,
                   os.path.join(cam_dir, "fine_curve.png"), "Camera %d - Fine Scan" % idx)

    picam2.stop()
    picam2.close()

    # Per-camera report
    report_path = os.path.join(cam_dir, "report.txt")
    with open(report_path, "w") as f:
        f.write("=== Arducam 64MP Focus Calibration Report (cam%d) ===\n" % idx)
        f.write("Mode: %s (%dx%d)  step=%.2f\n\n" % (args.mode, width, height, args.step))
        f.write("BEST LensPosition: %.2f  (sharpness=%.1f)\n" % (best_lp_final, best_score_final))
        f.write("  -> Use: python3 dual_cam_capture.py --camera %d --lens-position %.2f\n\n" % (idx, best_lp_final))
        f.write("--- Coarse scan ---\n")
        for lp, sc in zip(coarse_pos, coarse_scores):
            tag = " <-- coarse best" if lp == best_lp_coarse else ""
            f.write("  LP=%.2f  sharpness=%.1f%s\n" % (lp, sc, tag))
        if fine_pos:
            f.write("\n--- Fine scan (LP %.2f to %.2f, step %.2f) ---\n" % (
                fine_pos[0], fine_pos[-1], FINE_STEP))
            for lp, sc in zip(fine_pos, fine_scores):
                tag = " <-- BEST" if lp == best_lp_final else ""
                f.write("  LP=%.2f  sharpness=%.1f%s\n" % (lp, sc, tag))

    print("[Result] cam%d Best LensPosition = %.2f  (sharpness=%.1f)" % (idx, best_lp_final, best_score_final))
    return best_lp_final, best_score_final


def run_calibration():
    args = parse_args()
    indices = [0, 1] if args.camera == "both" else [int(args.camera)]

    out_root = os.path.join(SAVE_DIR_BASE, "calibration_%s" % timestamp())
    os.makedirs(out_root, exist_ok=True)
    print("Output: %s" % out_root)

    results = {}
    for idx in indices:
        results[idx] = calibrate_camera(idx, args, out_root)

    # Summary report
    summary_path = os.path.join(out_root, "summary.txt")
    with open(summary_path, "w") as f:
        f.write("=== Dual Camera Focus Calibration Summary ===\n\n")
        for idx in indices:
            best_lp, best_score = results[idx]
            f.write("Camera %d: best LensPosition=%.2f (sharpness=%.1f)\n" % (idx, best_lp, best_score))
            f.write("  -> python3 dual_cam_capture.py --camera %d --lens-position %.2f\n\n" % (idx, best_lp))

    print("\n=== Summary ===")
    for idx in indices:
        best_lp, best_score = results[idx]
        print("cam%d: LP=%.2f (sharpness=%.1f)" % (idx, best_lp, best_score))
    print("[Output] %s" % out_root)


if __name__ == "__main__":
    run_calibration()
