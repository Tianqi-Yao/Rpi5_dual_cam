#!/usr/bin/env python3
# Hybrid preview + focus control for IMX477 (Raspberry Pi HQ Camera) — manual lens version.
# Adapted from preview_focus_hybrid.py (OV64A40 version).
# No LP / AF controls — manual lens only.
#
# Usage: python3 preview_focus_hybrid_imx477.py
#        Edit CAM_IDX below to select camera.

import os
import time
import subprocess
from datetime import datetime
from picamera2 import Picamera2
import cv2
import numpy as np

# ── Hyperparameters ────────────────────────────────────────────────────────
CAM_IDX = 0          # 0 or 1

# ── Display ────────────────────────────────────────────────────────────────
STATUS_H  = 145      # pixels for status bar above image
DISPLAY_W = 1280
DISPLAY_H = 720
# DISPLAY_W = 4056
# DISPLAY_H = 3040
WIN_NAME  = "preview"

# rpicam-still subprocess window geometry (still backend only)
RPICAM_WIN_X = 100
RPICAM_WIN_Y = 50
RPICAM_WIN_W = DISPLAY_W
RPICAM_WIN_H = DISPLAY_H

# ── Camera / sensor (IMX477) ───────────────────────────────────────────────
EV_MIN  = -4.0
EV_MAX  =  4.0
ZOOM_MIN = 1
ZOOM_MAX = 20

SENSOR_W = 4056
SENSOR_H = 3040

CAPTURE_FULL = (4056, 3040)
CAPTURE_HALF = (2028, 1520)

DEBOUNCE_S    = 0.25
RESTART_DELAY = 0.15

FULL_MODE = "4056:3040:12:P"
HALF_MODE = "2028:1520:12:P"

OUTPUT_DIR_BASE = os.path.expanduser("~/Desktop/images/preview_captures")

INFO_FMT = (
    "Focus=%focus  FPS=%fps  "
    "Exp=%exp us  AG=%ag  DG=%dg"
)

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


# ── Helpers ────────────────────────────────────────────────────────────────

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ── State ──────────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.ev        = 0.0
        self.zoom      = 1
        self.zoom_cx   = 0.5
        self.zoom_cy   = 0.5
        self.save_full = True
        self.backend   = "qtgl"   # "qtgl" or "still"

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

    def save_mode_cmd(self):
        return FULL_MODE if self.save_full else HALF_MODE


# ── PicaBackend (Picamera2 + OpenCV) ──────────────────────────────────────

class PicaBackend:
    def __init__(self, cam_idx):
        self.cam_idx    = cam_idx
        self.cam        = None
        self.capture_sz = CAPTURE_HALF   # default preview resolution

    def start(self, state):
        self.cam = Picamera2(self.cam_idx)
        cfg = self.cam.create_preview_configuration(
            main={"size": self.capture_sz, "format": "RGB888"},
        )
        self.cam.configure(cfg)
        self.cam.start()
        time.sleep(1.5)
        self.cam.set_controls({"ExposureValue": state.ev})
        if state.zoom > 1:
            self.cam.set_controls({"ScalerCrop": state.scaler_crop()})

    def stop(self):
        if self.cam is not None:
            try: self.cam.stop()
            except Exception: pass
            try: self.cam.close()
            except Exception: pass
            self.cam = None
        time.sleep(0.3)

    def apply_controls(self, state):
        if self.cam:
            self.cam.set_controls({"ExposureValue": state.ev})

    def apply_zoom(self, state):
        if self.cam:
            self.cam.set_controls({"ScalerCrop": state.scaler_crop()})

    def grab_frame(self):
        if self.cam is None:
            return None
        try:
            frame = self.cam.capture_array()
            return cv2.resize(frame, (DISPLAY_W, DISPLAY_H))
        except Exception as e:
            print("[WARN] grab_frame error: %s" % e)
            return None

    def alive(self):
        return self.cam is not None

    def switch_capture_sz(self, new_sz, state):
        self.stop()
        self.capture_sz = new_sz
        self.start(state)


# ── StillBackend (rpicam-still subprocess) ─────────────────────────────────

class StillBackend:
    def __init__(self, cam_idx):
        self.cam_idx  = cam_idx
        self.proc     = None
        self._pending = False
        self._last_t  = 0.0

    def _cmd(self, state):
        x, y, w, h = state.roi()
        return [
            "rpicam-still", "-t", "0",
            "--camera", str(self.cam_idx),
            "--mode", HALF_MODE,
            "--preview", "%d,%d,%d,%d" % (RPICAM_WIN_X, RPICAM_WIN_Y, RPICAM_WIN_W, RPICAM_WIN_H),
            "--info-text", INFO_FMT,
            "--ev", "%.1f" % state.ev,
            "--roi", "%.4f,%.4f,%.4f,%.4f" % (x, y, w, h),
            "--verbose", "0",
        ]

    def start(self, state):
        self.proc = subprocess.Popen(
            self._cmd(state), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait()
        self.proc = None

    def restart(self, state):
        self.stop()
        time.sleep(RESTART_DELAY)
        self.start(state)
        self._pending = False

    def mark_dirty(self):
        self._pending = True
        self._last_t  = time.time()

    def tick(self, state):
        if not self.alive() and not self._pending:
            print("[WARN] rpicam-still died, restarting...")
            self.restart(state)
        if self._pending and (time.time() - self._last_t > DEBOUNCE_S):
            self.restart(state)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


# ── Status bar ─────────────────────────────────────────────────────────────

def make_status_bar(state, cam_idx, capture_sz):
    bar = np.zeros((STATUS_H, DISPLAY_W, 3), dtype=np.uint8)
    fs = 0.65
    fw = 2
    dy = 34

    line1a = (
        "cam%d  [%s]  EV=%+.1f  Zoom=%dx  IMX477 manual lens"
        % (cam_idx, state.backend.upper(), state.ev, state.zoom)
    )
    line1b = (
        "Save=%s  Preview=%dx%d-->%dx%d  Center=(%.2f, %.2f)"
        % ("FULL 12MP" if state.save_full else "HALF",
           capture_sz[0], capture_sz[1], DISPLAY_W, DISPLAY_H,
           state.zoom_cx, state.zoom_cy)
    )
    line2a = "e/w:EV  a/d:zoom  ijkl:pan  r:reset  p:cap  g:save  h:bknd  f:info  q:quit"
    line2b = "SAVE: z=rpi-jpg  x=rpi-png  c=rpi-dng  v=pic-jpg  b=pic-png  n=pic-dng  m=ALL  y=ev-brk"

    cv2.putText(bar, line1a, (8, dy),     cv2.FONT_HERSHEY_SIMPLEX, fs,   (0, 255, 0),     fw, cv2.LINE_AA)
    cv2.putText(bar, line1b, (8, dy * 2), cv2.FONT_HERSHEY_SIMPLEX, fs,   (0, 220, 0),     fw, cv2.LINE_AA)
    cv2.putText(bar, line2a, (8, dy * 3), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (160, 160, 160), fw, cv2.LINE_AA)
    cv2.putText(bar, line2b, (8, dy * 4), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (100, 200, 255), fw, cv2.LINE_AA)
    return bar


# ── Save helpers ───────────────────────────────────────────────────────────

def _stop_for_save(state, qbe, sbe):
    if state.backend == "qtgl":
        qbe.stop()
    else:
        sbe.stop()
        time.sleep(0.1)


def _restore_after_save(state, qbe, sbe):
    if state.backend == "qtgl":
        qbe.start(state)
    else:
        sbe.start(state)


def _rpicam_run(qbe, state, mode_str, out_path, extra=""):
    """Run rpicam-still. No -t → uses default 5000ms for AE convergence."""
    cmd = (
        'rpicam-still -n --camera %d --mode %s '
        '--ev %.1f%s -o "%s"'
        % (qbe.cam_idx, mode_str, state.ev, extra, out_path)
    )
    return os.system(cmd)


def _picamera_dng_capture(qbe, state, out_path):
    """Open Picamera2 with preview config + raw stream, save DNG."""
    size = CAPTURE_FULL if state.save_full else CAPTURE_HALF
    cam = Picamera2(qbe.cam_idx)
    try:
        cfg = cam.create_preview_configuration(
            main={"size": size, "format": "RGB888"},
            raw={"size": cam.sensor_resolution},
            buffer_count=2)
        cam.configure(cfg)
        cam.start()
        cam.set_controls({"ExposureValue": state.ev})
        time.sleep(5.0)
        request = cam.capture_request()
        try:
            request.save_dng(out_path)
        finally:
            request.release()
    finally:
        try: cam.stop()
        except Exception: pass
        try: cam.close()
        except Exception: pass


def _fname(base_ts, tag, ext, state, qbe):
    return "%s_%s_ev%.1f_cam%d.%s" % (
        base_ts, tag, state.ev, qbe.cam_idx, ext)


# ── 6 save routes ──────────────────────────────────────────────────────────

def save_r1(state, qbe, output_dir, base_ts=None):
    """z — rpicam-still → JPEG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "z_r1_rpicam_jpg", "jpg", state, qbe))
    print("[R1] rpi JPEG -> %s" % path)
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path)
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r2(state, qbe, output_dir, base_ts=None):
    """x — rpicam-still → PNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "x_r2_rpicam_png", "png", state, qbe))
    print("[R2] rpi PNG  -> %s" % path)
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, " --encoding png")
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r3(state, qbe, output_dir, base_ts=None):
    """c — rpicam-still → DNG + JPEG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "c_r3_rpicam_dng", "jpg", state, qbe))
    print("[R3] rpi DNG  -> %s + .dng" % path)
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, " --raw")
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r4(state, qbe, output_dir, base_ts=None):
    """v — Picamera2 → JPEG (from running preview)"""
    if qbe.cam is None:
        print("[R4] Skip: preview not running"); return
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "v_r4_picam_jpg", "jpg", state, qbe))
    frame = qbe.cam.capture_array()
    print("[R4] pic JPEG -> %s" % path)
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("     OK")


def save_r5(state, qbe, output_dir, base_ts=None):
    """b — Picamera2 → PNG (from running preview)"""
    if qbe.cam is None:
        print("[R5] Skip: preview not running"); return
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "b_r5_picam_png", "png", state, qbe))
    frame = qbe.cam.capture_array()
    print("[R5] pic PNG  -> %s" % path)
    cv2.imwrite(path, frame)
    print("     OK")


def save_r6(state, qbe, output_dir, base_ts=None):
    """n — Picamera2 → DNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "n_r6_picam_dng", "dng", state, qbe))
    print("[R6] pic DNG  -> %s" % path)
    _picamera_dng_capture(qbe, state, path)
    print("     OK")


def save_all(state, qbe, sbe, output_dir):
    """m — all 6 routes. R4/R5 from running preview, then stop for R1~R3/R6."""
    os.makedirs(output_dir, exist_ok=True)
    t = ts()
    print("[ALL] Starting all 6 routes  ts=%s" % t)

    save_r4(state, qbe, output_dir, t)
    save_r5(state, qbe, output_dir, t)

    _stop_for_save(state, qbe, sbe)
    try:
        save_r1(state, qbe, output_dir, t)
        save_r2(state, qbe, output_dir, t)
        save_r3(state, qbe, output_dir, t)
        save_r6(state, qbe, output_dir, t)
    finally:
        _restore_after_save(state, qbe, sbe)
    print("[ALL] Done.")


# ── EV bracket ─────────────────────────────────────────────────────────────

def ev_bracket(state, qbe, sbe, output_dir):
    """Picamera2 EV bracket: 5 shots sweeping EV."""
    os.makedirs(output_dir, exist_ok=True)
    offsets = [-1.0, -0.5, 0.0, 0.5, 1.0]
    evs     = [round(clamp(state.ev + d, EV_MIN, EV_MAX), 1) for d in offsets]
    print("[EV-BRACKET] EV: %s  (Picamera2 PNG)" % evs)

    _stop_for_save(state, qbe, sbe)
    try:
        size = CAPTURE_FULL if state.save_full else CAPTURE_HALF
        cam = Picamera2(qbe.cam_idx)
        try:
            cfg = cam.create_preview_configuration(
                main={"size": size, "format": "RGB888"}, buffer_count=2)
            cam.configure(cfg)
            cam.start()
            cam.set_controls({"ExposureValue": evs[0]})
            cold_s = 8.0 if state.save_full else 5.0
            warm_s = 4.0 if state.save_full else 1.5
            time.sleep(cold_s)
            base_ts = ts()
            for i, ev in enumerate(evs):
                if i > 0:
                    cam.set_controls({"ExposureValue": ev})
                    time.sleep(warm_s)
                frame = cam.capture_array()
                fname = "%s_bracket_ev%.1f_brk%d_cam%d.png" % (
                    base_ts, ev, i, qbe.cam_idx)
                cv2.imwrite(os.path.join(output_dir, fname), frame)
                print("  EV=%+.1f -> OK" % ev)
        finally:
            try: cam.stop()
            except Exception: pass
            try: cam.close()
            except Exception: pass
    finally:
        _restore_after_save(state, qbe, sbe)
    print("[EV-BRACKET] Done.")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    cam_idx    = CAM_IDX
    output_dir = os.path.join(OUTPUT_DIR_BASE, "cam%d" % cam_idx)
    os.makedirs(output_dir, exist_ok=True)

    state = State()

    qbe = PicaBackend(cam_idx)
    sbe = StillBackend(cam_idx)

    print("[INFO] Starting Picamera2 preview for cam%d (IMX477 manual lens)..." % cam_idx)
    qbe.start(state)
    print("[INFO] Output: %s" % output_dir)

    cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)

    try:
        while True:
            if state.backend == "qtgl":
                frame = qbe.grab_frame()
            else:
                sbe.tick(state)
                frame = np.zeros((DISPLAY_H, DISPLAY_W, 3), dtype=np.uint8)
                cv2.putText(frame,
                            "rpicam-still preview active (see separate window)",
                            (40, DISPLAY_H // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 200, 80), 2, cv2.LINE_AA)

            if frame is not None:
                bar = make_status_bar(state, cam_idx, qbe.capture_sz)
                cv2.imshow(WIN_NAME, np.vstack([bar, frame]))

            key = cv2.waitKey(10)
            if key == -1:
                continue
            k = key & 0xFF

            if k == ord('q'):
                break

            # ── EV ─────────────────────────────────────────────────────────
            elif k in (ord('e'), ord('E')):
                state.ev = round(clamp(state.ev + 0.5, EV_MIN, EV_MAX), 1)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif k in (ord('w'), ord('W')):
                state.ev = round(clamp(state.ev - 0.5, EV_MIN, EV_MAX), 1)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            # ── Zoom: a=out, d=in ──────────────────────────────────────────
            elif k in (ord('a'), ord('A')):
                state.zoom = clamp(state.zoom - 1, ZOOM_MIN, ZOOM_MAX)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif k in (ord('d'), ord('D')):
                state.zoom = clamp(state.zoom + 1, ZOOM_MIN, ZOOM_MAX)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # ── Pan: ijkl ─────────────────────────────────────────────────
            elif k == ord('i'):
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cy = clamp(state.zoom_cy - _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif k == ord('k'):
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cy = clamp(state.zoom_cy + _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif k == ord('j'):
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cx = clamp(state.zoom_cx - _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif k == ord('l'):
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cx = clamp(state.zoom_cx + _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # ── Reset zoom ─────────────────────────────────────────────────
            elif k in (ord('r'), ord('R')):
                state.zoom    = 1
                state.zoom_cx = 0.5
                state.zoom_cy = 0.5
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # ── ROI presets ────────────────────────────────────────────────
            elif k in ROI_PRESETS:
                cx, cy, w, h = ROI_PRESETS[k]
                state.zoom_cx = cx
                state.zoom_cy = cy
                state.zoom    = clamp(int(round(1.0 / w)) if w < 1.0 else 1,
                                      ZOOM_MIN, ZOOM_MAX)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # ── Preview capture resolution (p) ─────────────────────────────
            elif k in (ord('p'), ord('P')):
                if state.backend == "qtgl":
                    new_sz = CAPTURE_FULL if qbe.capture_sz == CAPTURE_HALF else CAPTURE_HALF
                    print("[CAP] Switching preview capture to %dx%d ..." % new_sz)
                    qbe.switch_capture_sz(new_sz, state)
                    print("[CAP] Done.")

            # ── Backend toggle (h) ─────────────────────────────────────────
            elif k in (ord('h'), ord('H')):
                if state.backend == "qtgl":
                    print("[MODE] Switching to rpicam-still preview...")
                    qbe.stop()
                    state.backend = "still"
                    sbe.start(state)
                    print("[MODE] rpicam-still active.")
                else:
                    print("[MODE] Switching to Picamera2 preview...")
                    sbe.stop()
                    time.sleep(0.3)
                    state.backend = "qtgl"
                    qbe.start(state)
                    print("[MODE] Picamera2 active.")

            # ── Save resolution toggle (g) ─────────────────────────────────
            elif k in (ord('g'), ord('G')):
                state.save_full = not state.save_full
                print("[RES] Save: %s" % (
                    "4056x3040 FULL" if state.save_full else "2028x1520 HALF"))

            # ── 6 save routes (z x c v b n) ───────────────────────────────
            elif k in (ord('z'), ord('Z')):
                os.makedirs(output_dir, exist_ok=True)
                _stop_for_save(state, qbe, sbe)
                try:
                    save_r1(state, qbe, output_dir)
                finally:
                    _restore_after_save(state, qbe, sbe)

            elif k in (ord('x'), ord('X')):
                os.makedirs(output_dir, exist_ok=True)
                _stop_for_save(state, qbe, sbe)
                try:
                    save_r2(state, qbe, output_dir)
                finally:
                    _restore_after_save(state, qbe, sbe)

            elif k in (ord('c'), ord('C')):
                os.makedirs(output_dir, exist_ok=True)
                _stop_for_save(state, qbe, sbe)
                try:
                    save_r3(state, qbe, output_dir)
                finally:
                    _restore_after_save(state, qbe, sbe)

            elif k in (ord('v'), ord('V')):
                os.makedirs(output_dir, exist_ok=True)
                save_r4(state, qbe, output_dir)

            elif k in (ord('b'), ord('B')):
                os.makedirs(output_dir, exist_ok=True)
                save_r5(state, qbe, output_dir)

            elif k in (ord('n'), ord('N')):
                os.makedirs(output_dir, exist_ok=True)
                _stop_for_save(state, qbe, sbe)
                try:
                    save_r6(state, qbe, output_dir)
                finally:
                    _restore_after_save(state, qbe, sbe)

            # ── All routes (m) ─────────────────────────────────────────────
            elif k in (ord('m'), ord('M')):
                save_all(state, qbe, sbe, output_dir)

            # ── EV bracket (y) ─────────────────────────────────────────────
            elif k in (ord('y'), ord('Y')):
                ev_bracket(state, qbe, sbe, output_dir)

            # ── Info (f) ───────────────────────────────────────────────────
            elif k in (ord('f'), ord('F')):
                roi = state.roi()
                print("[INFO] cam%d  backend=%s  EV=%+.1f  "
                      "Zoom=%dx  Center=(%.2f,%.2f)  "
                      "ROI=(%.3f,%.3f,%.3f,%.3f)  save=%s" % (
                    cam_idx, state.backend, state.ev,
                    state.zoom, state.zoom_cx, state.zoom_cy,
                    roi[0], roi[1], roi[2], roi[3],
                    "FULL" if state.save_full else "HALF"))

    except KeyboardInterrupt:
        pass
    finally:
        if state.backend == "qtgl":
            qbe.stop()
        else:
            sbe.stop()
        cv2.destroyAllWindows()
        print("[INFO] Exited.")


if __name__ == "__main__":
    main()
