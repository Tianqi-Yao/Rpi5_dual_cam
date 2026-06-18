"""
双摄两阶段焦距标定工具。
  Phase 1：粗扫（LP 0.0 → 16.0，默认 step=0.5）
  Phase 2：精扫（best_lp ± FINE_RANGE，step=0.1）
用 Laplacian 方差衡量清晰度，生成曲线图和报告。
双摄串行处理（cam0 close 后再开 cam1）。

用法：
  python3 calibration.py [--mode quick|normal|full] [--step 0.5] [--no-fine]
"""

import os
import sys
import time
import argparse
import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from picamera2 import Picamera2

from cam_common import (
    SENSOR_MODES, LP_MIN, LP_MAX, SAVE_DIR_BASE,
    clamp, timestamp, laplacian_sharpness,
)

SCAN_MODES = {
    "quick":  SENSOR_MODES["mid"],
    "normal": SENSOR_MODES["half"],
    "full":   SENSOR_MODES["full"],
}
SETTLE_TIMES = {"quick": 1.0, "normal": 2.0, "full": 5.0}

FINE_RANGE = 1.0
FINE_STEP = 0.1


def scan_range(picam2, positions, settle, out_dir, label):
    os.makedirs(out_dir, exist_ok=True)
    scores = []
    for lp in positions:
        picam2.set_controls({"AfMode": 0, "LensPosition": lp})
        time.sleep(settle)
        frame = picam2.capture_array()
        score = laplacian_sharpness(frame)
        scores.append(score)
        fname = f"{label}_lp{lp:.2f}_score{score:.1f}.jpg"
        cv2.imwrite(os.path.join(out_dir, fname), frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"  LP={lp:.2f}  score={score:.1f}")
    return scores


def plot_curve(positions, scores, best_lp, best_score, path, title):
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(positions, scores, "b.-", linewidth=1.5, markersize=5)
    ax.axvline(best_lp, color="r", linestyle="--", label=f"best LP={best_lp:.2f}")
    ax.scatter([best_lp], [best_score], color="r", zorder=5)
    ax.set_xlabel("LensPosition")
    ax.set_ylabel("Laplacian Variance")
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"  曲线图已保存 {path}")


def calibrate_camera(cam_idx, args, out_root):
    cam_dir = os.path.join(out_root, f"cam{cam_idx}")
    size = SCAN_MODES[args.mode]
    settle = SETTLE_TIMES[args.mode]

    cam = Picamera2(cam_idx)
    cfg = cam.create_still_configuration(
        main={"size": size, "format": "BGR888"},
        buffer_count=1,
    )
    cam.configure(cfg)
    cam.start()

    # Phase 1 粗扫
    print(f"\n[cam{cam_idx}] Phase 1 粗扫 step={args.step}")
    coarse_pos = list(np.arange(LP_MIN, LP_MAX + 1e-9, args.step))
    coarse_dir = os.path.join(cam_dir, "coarse")
    coarse_scores = scan_range(cam, coarse_pos, settle, coarse_dir, "coarse")

    best_idx = int(np.argmax(coarse_scores))
    best_lp_coarse = coarse_pos[best_idx]
    best_score_coarse = coarse_scores[best_idx]
    print(f"[cam{cam_idx}] 粗扫最佳 LP={best_lp_coarse:.2f}  score={best_score_coarse:.1f}")

    plot_curve(
        coarse_pos, coarse_scores,
        best_lp_coarse, best_score_coarse,
        os.path.join(cam_dir, "coarse_curve.png"),
        f"cam{cam_idx} Coarse Scan",
    )

    best_lp_final = best_lp_coarse

    # Phase 2 精扫
    if not args.no_fine:
        print(f"\n[cam{cam_idx}] Phase 2 精扫 around LP={best_lp_coarse:.2f}")
        fine_start = clamp(best_lp_coarse - FINE_RANGE, LP_MIN, LP_MAX)
        fine_end = clamp(best_lp_coarse + FINE_RANGE, LP_MIN, LP_MAX)
        fine_pos = list(np.arange(fine_start, fine_end + 1e-9, FINE_STEP))
        fine_dir = os.path.join(cam_dir, "fine")
        fine_scores = scan_range(cam, fine_pos, settle, fine_dir, "fine")

        best_fidx = int(np.argmax(fine_scores))
        best_lp_fine = fine_pos[best_fidx]
        best_score_fine = fine_scores[best_fidx]
        print(f"[cam{cam_idx}] 精扫最佳 LP={best_lp_fine:.2f}  score={best_score_fine:.1f}")

        plot_curve(
            fine_pos, fine_scores,
            best_lp_fine, best_score_fine,
            os.path.join(cam_dir, "fine_curve.png"),
            f"cam{cam_idx} Fine Scan",
        )
        best_lp_final = best_lp_fine

    cam.stop()
    cam.close()

    # 报告
    report_path = os.path.join(cam_dir, "report.txt")
    os.makedirs(cam_dir, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(f"cam{cam_idx} 焦距标定报告\n")
        f.write(f"模式: {args.mode} ({size[0]}x{size[1]})\n")
        f.write(f"粗扫最佳 LP: {best_lp_coarse:.2f}  score={best_score_coarse:.1f}\n")
        if not args.no_fine:
            f.write(f"精扫最佳 LP: {best_lp_final:.2f}\n")
        f.write(f"建议使用 LP: {best_lp_final:.2f}\n")
    print(f"[cam{cam_idx}] 报告已保存 {report_path}")
    return best_lp_final


def main():
    parser = argparse.ArgumentParser(description="双摄焦距标定")
    parser.add_argument("--mode", choices=["quick", "normal", "full"],
                        default="normal", help="扫描分辨率")
    parser.add_argument("--step", type=float, default=0.5, help="粗扫步长")
    parser.add_argument("--no-fine", action="store_true", help="跳过精扫")
    args = parser.parse_args()

    out_root = os.path.join(SAVE_DIR_BASE, f"calibration_{timestamp()}")
    os.makedirs(out_root, exist_ok=True)
    print(f"输出目录: {out_root}")

    results = {}
    for cam_idx in range(2):
        best_lp = calibrate_camera(cam_idx, args, out_root)
        results[cam_idx] = best_lp

    summary_path = os.path.join(out_root, "summary.txt")
    with open(summary_path, "w") as f:
        f.write("双摄标定汇总\n")
        for idx, lp in results.items():
            f.write(f"  cam{idx}: 建议 LP = {lp:.2f}\n")
    print(f"\n汇总已保存 {summary_path}")
    for idx, lp in results.items():
        print(f"  cam{idx}: 建议 LP = {lp:.2f}")


if __name__ == "__main__":
    main()
