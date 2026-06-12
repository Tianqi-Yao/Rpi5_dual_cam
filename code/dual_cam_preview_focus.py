#!/usr/bin/env python3
# Interactive dual-camera preview + focus/exposure control + capture
# for two Arducam 64MP (OV64A40) on Raspberry Pi 5.
#
# Single OpenCV window, two preview streams side by side (np.hstack).
# On Rpi5, Picamera2 can capture the full 64MP frame directly (no OOM),
# so the default save backend is Picamera2 itself. rpicam-still subprocess
# is kept as an optional backend (press V) for image-quality comparison.
#
# Usage: python3 dual_cam_preview_focus.py

import os
import time
import subprocess
import numpy as np
import cv2
from picamera2 import Picamera2

from dual_cam_common import (
    SENSOR_MODES, RPICAM_MODE_STR, SENSOR_W, SENSOR_H,
    LP_MIN, LP_MAX as LP_USER_MAX, EV_MIN, EV_MAX,
    clamp, timestamp, laplacian_sharpness, SAVE_DIR_BASE,
)

# -- Config --
PREVIEW_SIZE  = (960, 720)
INIT_LP       = 5.0
ZOOM_MIN      = 1
ZOOM_MAX      = 20

# Preview raw (sensor) mode cycle — all 4:3, no FOV crop. 'p' cycles through these.
PREVIEW_MODES = ["mid", "half", "full"]
PREVIEW_FPS   = {"mid": 30, "half": 7, "full": 2}

OUTPUT_DIR = os.path.join(SAVE_DIR_BASE, "preview_captures")

ROI_PRESETS = {
    ord('`'): (0.50, 0.50, 1.00, 1.00),
    ord('1'): (0.50, 0.50, 0.50, 0.50),
    ord('2'): (0.25, 0.25, 0.50, 0.50),
    ord('3'): (0.75, 0.25, 0.50, 0.50),
    ord('4'): (0.25, 0.75, 0.50, 0.50),
    ord('5'): (0.75, 0.75, 0.50, 0.50),
    ord('6'): (0.50, 0.50, 0.25, 0.25),
    ord('7'): (0.25, 0.25, 0.25, 0.25),
    ord('8'): (0.75, 0.25, 0.25, 0.25),
    ord('9'): (0.25, 0.75, 0.25, 0.25),
    ord('0'): (0.75, 0.75, 0.25, 0.25),
}


# -- Per-camera state --
class CamState:
    def __init__(self, idx):
        self.idx = idx
        self.lp = INIT_LP
        self.lp_max = LP_USER_MAX
        self.ev = 0.0
        self.zoom = 1
        self.zoom_cx = 0.5
        self.zoom_cy = 0.5
        self.save_full = True  # True=64MP (full), False=16MP (half)
        self.preview_mode = "mid"  # raw sensor mode for preview: mid/half/full

    def roi(self):
        if self.zoom <= 1:
            return (0.0, 0.0, 1.0, 1.0)
        w = clamp(1.0 / self.zoom, 0.05, 1.0)
        h = clamp(1.0 / self.zoom, 0.05, 1.0)
        x = clamp(self.zoom_cx - w / 2, 0.0, 1.0 - w)
        y = clamp(self.zoom_cy - h / 2, 0.0, 1.0 - h)
        return (x, y, w, h)

    def scaler_crop(self):
        rx, ry, rw, rh = self.roi()
        pw = max(int(rw * SENSOR_W), 64)
        ph = max(int(rh * SENSOR_H), 64)
        px = clamp(int(rx * SENSOR_W), 0, SENSOR_W - pw)
        py = clamp(int(ry * SENSOR_H), 0, SENSOR_H - ph)
        return (px, py, pw, ph)

    def save_mode(self):
        return "full" if self.save_full else "half"

    def save_res_tag(self):
        return "64mp" if self.save_full else "16mp"


# -- Camera manager --
class DualCam:
    def __init__(self):
        self.cams = [Picamera2(0), Picamera2(1)]
        self.states = [CamState(0), CamState(1)]
        for cam, st in zip(self.cams, self.states):
            try:
                ctrl = cam.camera_controls.get("LensPosition")
                if ctrl and len(ctrl) >= 2 and ctrl[1] > 0:
                    st.lp_max = min(float(ctrl[1]), LP_USER_MAX)
            except Exception:
                pass
            self._start_preview(cam, st)

    def _preview_config(self, cam, mode):
        return cam.create_preview_configuration(
            main={"size": PREVIEW_SIZE, "format": "BGR888"},
            raw={"size": SENSOR_MODES[mode]},
            controls={"FrameRate": PREVIEW_FPS[mode]},
            buffer_count=2,
        )

    def _start_preview(self, cam, st):
        cfg = self._preview_config(cam, st.preview_mode)
        cam.configure(cfg)
        cam.start()
        time.sleep(0.3)
        cam.set_controls({"AfMode": 0, "LensPosition": st.lp, "ExposureValue": st.ev})
        if st.zoom > 1:
            cam.set_controls({"ScalerCrop": st.scaler_crop()})

    def cycle_preview_mode(self, idx):
        """Cycle the preview's raw sensor mode (mid -> half -> full -> mid)."""
        cam = self.cams[idx]
        st = self.states[idx]
        cur = PREVIEW_MODES.index(st.preview_mode)
        st.preview_mode = PREVIEW_MODES[(cur + 1) % len(PREVIEW_MODES)]
        cam.stop()
        self._start_preview(cam, st)
        return st.preview_mode

    def apply_controls(self, idx):
        st = self.states[idx]
        self.cams[idx].set_controls({
            "AfMode": 0, "LensPosition": st.lp, "ExposureValue": st.ev,
        })

    def apply_zoom(self, idx):
        self.cams[idx].set_controls({"ScalerCrop": self.states[idx].scaler_crop()})

    def capture_full(self, idx, mode=None):
        """Stop preview, capture full-res frame via Picamera2, restore preview."""
        cam = self.cams[idx]
        st = self.states[idx]
        mode = mode or st.save_mode()
        cam.stop()
        cfg = cam.create_still_configuration(
            main={"size": SENSOR_MODES[mode], "format": "BGR888"},
            buffer_count=1,
        )
        cam.configure(cfg)
        cam.start()
        cam.set_controls({"AfMode": 0, "LensPosition": st.lp, "ExposureValue": st.ev})
        time.sleep(0.5)
        frame = cam.capture_array()
        cam.stop()
        self._start_preview(cam, st)
        return frame

    def capture_rpicam_still(self, idx, path, mode=None):
        """Stop Picamera2 (releases device), capture via rpicam-still, restore preview."""
        cam = self.cams[idx]
        st = self.states[idx]
        mode = mode or st.save_mode()
        cam.stop()
        time.sleep(0.2)
        cmd = [
            "rpicam-still", "-n", "--immediate",
            "--camera", str(idx),
            "--mode", RPICAM_MODE_STR[mode],
            "--autofocus-mode", "manual",
            "--lens-position", "%.2f" % st.lp,
            "--ev", "%.1f" % st.ev,
            "-o", path,
        ]
        ret = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        time.sleep(0.2)
        self._start_preview(cam, st)
        return ret

    def autofocus_once(self, idx):
        cam = self.cams[idx]
        st = self.states[idx]
        cam.set_controls({"AfMode": 1, "AfRange": 2, "AfTrigger": 0})
        af_state = 0
        for _ in range(80):
            time.sleep(0.1)
            md = cam.capture_metadata()
            af_state = md.get("AfState", 0)
            if af_state in (2, 3):
                break
        md = cam.capture_metadata()
        lp = md.get("LensPosition")
        if lp is not None:
            st.lp = round(clamp(float(lp), LP_MIN, st.lp_max), 2)
        cam.set_controls({"AfMode": 0, "LensPosition": st.lp})
        return af_state

    def close(self):
        for cam in self.cams:
            try:
                cam.stop()
            except Exception:
                pass
            try:
                cam.close()
            except Exception:
                pass


# -- Save helpers --
def save_single(dual, idx, backend):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    st = dual.states[idx]
    fname = "%s_%s_lp%.2f_ev%.1f_cam%d.jpg" % (timestamp(), st.save_res_tag(), st.lp, st.ev, idx)
    path = os.path.join(OUTPUT_DIR, fname)
    print("\n[SAVE] cam%d %s LP=%.2f EV=%+.1f backend=%s ..." % (
        idx, st.save_mode(), st.lp, st.ev, backend))
    if backend == "rpicam-still":
        ret = dual.capture_rpicam_still(idx, path)
        if ret == 0:
            print("[SAVE] Done: %s" % path)
        else:
            print("[SAVE] Error (exit %d)" % ret)
    else:
        frame = dual.capture_full(idx)
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print("[SAVE] Done: %s" % path)


def save_both(dual, backend):
    for idx in range(2):
        save_single(dual, idx, backend)


def save_burst(dual, idx, backend, count=5):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    st = dual.states[idx]
    step = 0.25
    half = count // 2
    offsets = [round(-half * step + i * step, 2) for i in range(count)]
    lps = [round(clamp(st.lp + d, LP_MIN, st.lp_max), 2) for d in offsets]
    print("\n[BURST] cam%d  %d shots, LP: %s" % (idx, count, lps))
    base_lp = st.lp
    for lp in lps:
        st.lp = lp
        fname = "%s_%s_lp%.2f_ev%.1f_cam%d.jpg" % (timestamp(), st.save_res_tag(), lp, st.ev, idx)
        path = os.path.join(OUTPUT_DIR, fname)
        if backend == "rpicam-still":
            ret = dual.capture_rpicam_still(idx, path)
            ok = (ret == 0)
        else:
            frame = dual.capture_full(idx)
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            ok = True
        print("  LP=%.2f -> %s%s" % (lp, fname, "" if ok else "  [ERROR]"))
    st.lp = base_lp
    if backend == "picamera2":
        dual.apply_controls(idx)
    print("[BURST] Done. Output: %s" % OUTPUT_DIR)


def ev_bracket(dual, idx, backend):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    st = dual.states[idx]
    offsets = [-1.0, -0.5, 0.0, 0.5, 1.0]
    evs = [round(clamp(st.ev + d, EV_MIN, EV_MAX), 1) for d in offsets]
    print("\n[EV-BRACKET] cam%d  LP=%.2f  EV values: %s" % (idx, st.lp, evs))
    base_ev = st.ev
    for i, ev in enumerate(evs):
        st.ev = ev
        fname = "%s_%s_lp%.2f_ev%.1f_brk%d_cam%d.jpg" % (
            timestamp(), st.save_res_tag(), st.lp, ev, i, idx)
        path = os.path.join(OUTPUT_DIR, fname)
        if backend == "rpicam-still":
            ret = dual.capture_rpicam_still(idx, path)
            ok = (ret == 0)
        else:
            frame = dual.capture_full(idx)
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            ok = True
        print("  EV=%+.1f -> %s%s" % (ev, fname, "" if ok else "  [ERROR]"))
    st.ev = base_ev
    if backend == "picamera2":
        dual.apply_controls(idx)
    print("[EV-BRACKET] Done. Output: %s" % OUTPUT_DIR)


# -- Overlay --
def add_overlay(frame, idx, st, fps, sharpness, active):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 72), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)
    cv2.putText(frame, "CAM %d  SAVE=%s  PRE=%s" % (idx, st.save_res_tag().upper(), st.preview_mode.upper()),
                (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(frame, "LP=%.2f  EV=%+.1f  Zoom=%dx" % (st.lp, st.ev, st.zoom),
                (8, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 120), 2)
    cv2.putText(frame, "FPS=%.1f  Sharp=%.0f" % (fps, sharpness),
                (8, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 120), 2)
    if active:
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 165, 255), 4)
    return frame


def print_controls():
    print()
    print("=" * 64)
    print("  DUAL CAMERA PREVIEW + FOCUS  (Tab = switch active camera)")
    print("  LP: 0.0=infinity, higher=closer")
    print("=" * 64)
    print("  Focus (active camera)")
    print("    = / -   LP +0.1 / -0.1     ] / [   LP +0.5 / -0.5")
    print("    . / ,   LP +1.0 / -1.0     t       one-shot autofocus")
    print("  Exposure")
    print("    e / w   EV +0.5 / -0.5")
    print("  Zoom / Pan (ScalerCrop)")
    print("    z / x   zoom in / out      i/k/j/l  pan up/down/left/right")
    print("    r       reset zoom         `,1-0    ROI presets")
    print("  Capture")
    print("    s       save single (active camera)")
    print("    S       save single (BOTH cameras)")
    print("    b       burst 5 shots LP+/-0.25 (active camera)")
    print("    n       EV bracket 5 shots (active camera)")
    print("    m       toggle save resolution FULL(64MP) <-> HALF(16MP)")
    print("  Mode")
    print("    p       cycle preview raw mode: mid(4MP) -> half(16MP) -> full(64MP)")
    print("    v       toggle save backend: picamera2 <-> rpicam-still")
    print("  Other")
    print("    f       print state    h  help    q  quit")
    print("=" * 64)
    print()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    dual = DualCam()
    active = 0
    backend = "picamera2"

    win_name = "Dual Camera 64MP  |  Tab=switch cam  h=help  q=quit"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)

    print_controls()
    print("[INFO] LP range cam0=%.2f cam1=%.2f  Output: %s" % (
        dual.states[0].lp_max, dual.states[1].lp_max, OUTPUT_DIR))

    frame_count = 0
    t_start = time.monotonic()
    fps = 0.0

    try:
        while True:
            frames = []
            for i, cam in enumerate(dual.cams):
                frame = cam.capture_array()
                st = dual.states[i]
                sharp = laplacian_sharpness(frame)
                frame = add_overlay(frame, i, st, fps, sharp, active=(i == active))
                frames.append(frame)

            combined = np.hstack(frames)
            cv2.imshow(win_name, combined)

            frame_count += 1
            if frame_count % 30 == 0:
                fps = 30 / (time.monotonic() - t_start)
                t_start = time.monotonic()

            key = cv2.waitKey(1) & 0xFF
            if key == 255:
                continue

            st = dual.states[active]

            # -- Quit
            if key in (ord('q'), ord('Q')):
                break

            # -- Switch active camera
            elif key == 9:  # Tab
                active = 1 - active
                print("\n[ACTIVE] cam%d" % active)

            # -- Focus
            elif key == ord('='):
                st.lp = round(clamp(st.lp + 0.1, LP_MIN, st.lp_max), 2)
                dual.apply_controls(active)
            elif key == ord('-'):
                st.lp = round(clamp(st.lp - 0.1, LP_MIN, st.lp_max), 2)
                dual.apply_controls(active)
            elif key == ord(']'):
                st.lp = round(clamp(st.lp + 0.5, LP_MIN, st.lp_max), 2)
                dual.apply_controls(active)
            elif key == ord('['):
                st.lp = round(clamp(st.lp - 0.5, LP_MIN, st.lp_max), 2)
                dual.apply_controls(active)
            elif key == ord('.'):
                st.lp = round(clamp(st.lp + 1.0, LP_MIN, st.lp_max), 2)
                dual.apply_controls(active)
            elif key == ord(','):
                st.lp = round(clamp(st.lp - 1.0, LP_MIN, st.lp_max), 2)
                dual.apply_controls(active)

            # -- Exposure
            elif key in (ord('e'), ord('E')):
                st.ev = round(clamp(st.ev + 0.5, EV_MIN, EV_MAX), 1)
                dual.apply_controls(active)
            elif key in (ord('w'), ord('W')):
                st.ev = round(clamp(st.ev - 0.5, EV_MIN, EV_MAX), 1)
                dual.apply_controls(active)

            # -- Zoom
            elif key in (ord('z'), ord('Z')):
                st.zoom = clamp(st.zoom + 1, ZOOM_MIN, ZOOM_MAX)
                dual.apply_zoom(active)
            elif key in (ord('x'), ord('X')):
                st.zoom = clamp(st.zoom - 1, ZOOM_MIN, ZOOM_MAX)
                dual.apply_zoom(active)

            # -- Pan
            elif key == ord('i'):
                ps = 0.1 / max(st.zoom, 1)
                st.zoom_cy = clamp(st.zoom_cy - ps, 0.05, 0.95)
                dual.apply_zoom(active)
            elif key == ord('k'):
                ps = 0.1 / max(st.zoom, 1)
                st.zoom_cy = clamp(st.zoom_cy + ps, 0.05, 0.95)
                dual.apply_zoom(active)
            elif key == ord('j'):
                ps = 0.1 / max(st.zoom, 1)
                st.zoom_cx = clamp(st.zoom_cx - ps, 0.05, 0.95)
                dual.apply_zoom(active)
            elif key == ord('l'):
                ps = 0.1 / max(st.zoom, 1)
                st.zoom_cx = clamp(st.zoom_cx + ps, 0.05, 0.95)
                dual.apply_zoom(active)

            # -- Reset zoom
            elif key in (ord('r'), ord('R')):
                st.zoom, st.zoom_cx, st.zoom_cy = 1, 0.5, 0.5
                dual.apply_zoom(active)

            # -- ROI presets
            elif key in ROI_PRESETS:
                cx, cy, w, h = ROI_PRESETS[key]
                st.zoom_cx, st.zoom_cy = cx, cy
                st.zoom = clamp(int(round(1.0 / w)) if w < 1.0 else 1, ZOOM_MIN, ZOOM_MAX)
                dual.apply_zoom(active)

            # -- Toggle save resolution
            elif key in (ord('m'), ord('M')):
                st.save_full = not st.save_full
                print("\n[RES] cam%d save: %s" % (active, "FULL 9248x6944" if st.save_full else "HALF 4624x3472"))

            # -- Cycle preview raw mode
            elif key in (ord('p'), ord('P')):
                mode = dual.cycle_preview_mode(active)
                w, h = SENSOR_MODES[mode]
                print("\n[PREVIEW] cam%d raw mode -> %s (%dx%d, ~%dfps)" % (
                    active, mode, w, h, PREVIEW_FPS[mode]))

            # -- Toggle save backend
            elif key in (ord('v'), ord('V')):
                backend = "rpicam-still" if backend == "picamera2" else "picamera2"
                print("\n[BACKEND] save backend: %s" % backend)

            # -- Autofocus
            elif key in (ord('t'), ord('T')):
                print("\n[AF] cam%d one-shot autofocus..." % active)
                af_state = dual.autofocus_once(active)
                print("[AF] %s  LP=%.2f" % ("OK" if af_state == 2 else "Failed", st.lp))

            # -- Capture
            elif key == ord('s'):
                save_single(dual, active, backend)
            elif key == ord('S'):
                save_both(dual, backend)
            elif key == ord('b'):
                save_burst(dual, active, backend)
            elif key in (ord('n'), ord('N')):
                ev_bracket(dual, active, backend)

            # -- Help / info
            elif key in (ord('h'), ord('H')):
                print_controls()
            elif key in (ord('f'), ord('F')):
                roi = st.roi()
                print("\n[INFO] active=cam%d  LP=%.2f  EV=%+.1f  Zoom=%dx  "
                      "ROI=(%.3f,%.3f,%.3f,%.3f)  save=%s  preview=%s  backend=%s" % (
                          active, st.lp, st.ev, st.zoom,
                          roi[0], roi[1], roi[2], roi[3],
                          "FULL" if st.save_full else "HALF", st.preview_mode.upper(), backend))

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted")
    finally:
        dual.close()
        cv2.destroyAllWindows()
        print("[INFO] Cameras released")


if __name__ == "__main__":
    main()
