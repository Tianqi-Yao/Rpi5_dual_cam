#!/usr/bin/env python3
# Command-line single/dual photo capture for two Arducam 64MP (OV64A40) on Raspberry Pi 5.
#
# Default backend "picamera2" captures full resolution (up to 64MP) directly via
# Picamera2 (no OOM on Rpi5). Backend "rpicam-still" spawns the rpicam-still
# subprocess instead (kept for image-quality comparison / compatibility).
#
# Sensor modes (OV64A40):
#   full   9248x6944  ~2fps    64MP, max quality
#   half   4624x3472  ~7.6fps  16MP
#   4k     3840x2160  ~14.8fps 16:9 crop
#   mid    2312x1736  ~26.7fps  4MP
#   1080p  1920x1080  ~45fps   16:9 crop
#
# LensPosition: 0.0 = infinity, higher = closer (~9-10cm at max)
#
# Usage:
#   python3 dual_cam_capture.py
#   python3 dual_cam_capture.py --camera both --mode full --af
#   python3 dual_cam_capture.py --camera 0 --mode full --lens-position 5.0 --ev 0.5
#   python3 dual_cam_capture.py --camera both --backend rpicam-still --mode half

import argparse
import os
import time
from datetime import datetime

import cv2

from dual_cam_common import SENSOR_MODES, RPICAM_MODE_STR, SAVE_DIR_BASE


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture photos from one or both Arducam 64MP cameras (Rpi5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes: full(64MP ~2fps)  half(16MP ~7.6fps)  "
            "4k(~14.8fps)  mid(4MP ~26.7fps)  1080p(~45fps)\n"
            "LensPosition: 0.0=infinity  higher=closer"
        ),
    )
    parser.add_argument("--camera", choices=["0", "1", "both"], default="both",
                         help="Which camera to use (default: both)")
    parser.add_argument("--mode", choices=list(SENSOR_MODES.keys()), default="full",
                         help="Sensor mode (default: full = 64MP)")
    parser.add_argument("--lens-position", type=float, default=None,
                         help="Manual focus: LensPosition 0.0 (inf) to ~16.0 (closest)")
    parser.add_argument("--af", action="store_true",
                         help="Autofocus (overrides --lens-position)")
    parser.add_argument("--af-time", type=int, default=5000,
                         help="Autofocus wait time in ms (default: 5000)")
    parser.add_argument("--ev", type=float, default=0.0,
                         help="Exposure compensation EV (-4.0 to +4.0, default: 0.0)")
    parser.add_argument("--sharpness", type=float, default=1.0,
                         help="Sharpness (default: 1.0)")
    parser.add_argument("--backend", choices=["picamera2", "rpicam-still"], default="picamera2",
                         help="Capture backend (default: picamera2)")
    parser.add_argument("-o", "--output", type=str, default=None,
                         help="Output file path. With --camera both, _cam0/_cam1 is "
                              "inserted before the extension (or appended) for each camera.")
    return parser.parse_args()


def auto_output_name(args, idx):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    use_af = args.af or args.lens_position is None
    lp_tag = "_af" if use_af else ("_lp%.2f" % args.lens_position)
    ev_tag = ("_ev%.1f" % args.ev) if args.ev != 0.0 else ""
    return "%s_%s%s%s_cam%d.jpg" % (ts, args.mode, lp_tag, ev_tag, idx)


def output_path_for(args, idx, n_cams):
    if args.output is None:
        out_dir = os.path.join(SAVE_DIR_BASE, "captures")
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, auto_output_name(args, idx))

    if n_cams == 1:
        out_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(out_dir, exist_ok=True)
        return args.output

    root, ext = os.path.splitext(args.output)
    path = "%s_cam%d%s" % (root, idx, ext or ".jpg")
    out_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    return path


# -- picamera2 backend --
def capture_picamera2(idx, args, output):
    from picamera2 import Picamera2

    size = SENSOR_MODES[args.mode]
    cam = Picamera2(idx)
    try:
        cfg = cam.create_still_configuration(
            main={"size": size, "format": "BGR888"}, buffer_count=1,
        )
        cam.configure(cfg)
        cam.start()

        if args.af:
            print("[cam%d] Autofocus, waiting %dms..." % (idx, args.af_time))
            cam.set_controls({"AfMode": 2})
            time.sleep(args.af_time / 1000.0)
            md = cam.capture_metadata()
            lp = md.get("LensPosition", 0.0)
            cam.set_controls({"AfMode": 0, "LensPosition": lp})
        else:
            lp = args.lens_position if args.lens_position is not None else 0.0
            print("[cam%d] Manual focus: LensPosition=%.2f" % (idx, lp))
            cam.set_controls({"AfMode": 0, "LensPosition": lp})

        cam.set_controls({"ExposureValue": args.ev, "Sharpness": args.sharpness})
        time.sleep(0.3)

        frame = cam.capture_array()
        cv2.imwrite(output, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print("[cam%d] Mode=%s  EV=%.1f  Sharpness=%.1f  LP=%.2f" % (
            idx, args.mode, args.ev, args.sharpness, lp))
        print("[cam%d] Saved: %s" % (idx, output))
    finally:
        cam.stop()
        cam.close()


# -- rpicam-still backend --
def capture_rpicam_still(idx, args, output):
    mode_str = RPICAM_MODE_STR[args.mode]
    use_af = args.af or args.lens_position is None

    base = "rpicam-still -n --camera %d --mode %s --ev %.1f --sharpness %.1f -o %s" % (
        idx, mode_str, args.ev, args.sharpness, output)

    if use_af:
        cmd = "%s -t %d --autofocus-mode auto --autofocus-range macro" % (base, args.af_time)
        print("[cam%d] Autofocus, waiting %dms..." % (idx, args.af_time))
    else:
        cmd = "%s --immediate --autofocus-mode manual --lens-position %.2f" % (base, args.lens_position)
        print("[cam%d] Manual focus: LensPosition=%.2f" % (idx, args.lens_position))

    print("[cam%d] Mode=%s  EV=%.1f  Sharpness=%.1f" % (idx, args.mode, args.ev, args.sharpness))
    ret = os.system(cmd)
    if ret == 0:
        print("[cam%d] Saved: %s" % (idx, output))
    else:
        print("[cam%d] Error: rpicam-still failed (exit code %d)" % (idx, ret))


def main():
    args = parse_args()
    indices = [0, 1] if args.camera == "both" else [int(args.camera)]
    n_cams = len(indices)

    for idx in indices:
        output = output_path_for(args, idx, n_cams)
        if args.backend == "picamera2":
            capture_picamera2(idx, args, output)
        else:
            capture_rpicam_still(idx, args, output)


if __name__ == "__main__":
    main()
