#!/usr/bin/env python3
# Hybrid preview + focus control for Arducam 64MP (OV64A40) — Rpi5 dual-cam version.
# Adapted from ../64mp/cam_test/preview_focus_hybrid.py
#
# Default: Picamera2 OpenCV preview (status bar above image, instant LP/EV).
# Press H to switch to rpicam-still preview (own window; OpenCV window shows status only).
#
# LensPosition: 0.0 = infinity, higher = closer (~9-10cm at max)
#
# Usage: python3 preview_focus_hybrid.py
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
# DISPLAY_W = 4624
# DISPLAY_H = 3472
WIN_NAME  = "preview"

# rpicam-still subprocess window geometry (still backend only)
RPICAM_WIN_X = 100
RPICAM_WIN_Y = 50
RPICAM_WIN_W = DISPLAY_W
RPICAM_WIN_H = DISPLAY_H

# ── Camera / focus ─────────────────────────────────────────────────────────
INIT_LP     = 15.0
LP_MIN      = 0.0
LP_USER_MAX = 16.0
LP_MAX      = LP_USER_MAX   # updated at startup from camera_controls
EV_MIN      = -4.0
EV_MAX      =  4.0
ZOOM_MIN    = 1
ZOOM_MAX    = 20

SENSOR_W = 9248
SENSOR_H = 6944

DEBOUNCE_S    = 0.25
RESTART_DELAY = 0.15

FULL_MODE = "9248:6944:12:P"
HALF_MODE = "4624:3472:12:P"

OUTPUT_DIR_BASE = os.path.expanduser("~/Desktop/images/preview_captures")

INFO_FMT = (
    "LP=%lp  Focus=%focus  FPS=%fps  "
    "Exp=%exp us  AG=%ag  DG=%dg  AF=%afstate"
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


def _read_lp_max(cam):
    try:
        ctrl = cam.camera_controls.get("LensPosition")
        if ctrl and len(ctrl) >= 2 and ctrl[1] > 0:
            return min(float(ctrl[1]), LP_USER_MAX)
    except Exception:
        pass
    return LP_USER_MAX


# ── State ──────────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.lp        = INIT_LP
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

    def save_res_tag(self):
        return "64mp" if self.save_full else "half"


# ── PicaBackend (Picamera2 + OpenCV) ──────────────────────────────────────

class PicaBackend:
    def __init__(self, cam_idx):
        self.cam_idx    = cam_idx
        self.cam        = None
        self.capture_sz = (4624, 3472)

    def start(self, state):
        global LP_MAX
        self.cam = Picamera2(self.cam_idx)
        LP_MAX = _read_lp_max(self.cam)
        cfg = self.cam.create_preview_configuration(
            main={"size": self.capture_sz, "format": "RGB888"},
        )
        self.cam.configure(cfg)
        self.cam.start()
        time.sleep(1.5)
        self.cam.set_controls({
            "AfMode": 0,
            "LensPosition": state.lp,
            "ExposureValue": state.ev,
        })
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
            self.cam.set_controls({
                "AfMode": 0,
                "LensPosition": state.lp,
                "ExposureValue": state.ev,
            })

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
            "--mode", "4624:3472:12:P",
            "--preview", "%d,%d,%d,%d" % (RPICAM_WIN_X, RPICAM_WIN_Y, RPICAM_WIN_W, RPICAM_WIN_H),
            "--info-text", INFO_FMT,
            "--autofocus-mode", "manual",
            "--lens-position", "%.2f" % state.lp,
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

def make_status_bar(state, cam_idx, lp_max, capture_sz=(4624, 3472)):
    bar = np.zeros((STATUS_H, DISPLAY_W, 3), dtype=np.uint8)
    fs = 0.65
    fw = 2
    dy = 34

    line1a = (
        "cam%d  [%s]  LP=%.2f / max=%.2f  EV=%+.1f  Zoom=%dx"
        % (cam_idx, state.backend.upper(), state.lp, lp_max, state.ev, state.zoom)
    )
    line1b = (
        "Save=%s  Preview=%dx%d-->%dx%d  Center=(%.2f, %.2f)"
        % ("FULL 64MP" if state.save_full else "HALF 16MP",
           capture_sz[0], capture_sz[1], DISPLAY_W, DISPLAY_H,
           state.zoom_cx, state.zoom_cy)
    )
    line2a = ("=/- ] [ . , : LP    e/w : EV    a/d : zoom    i/k/j/l : pan    r : reset    "
              "p : cap res    g : save res    h : backend    t : AF    f : info    q : quit")
    line2b = ("SAVE: z=rpi JPEG  x=rpi PNG  c=rpi DNG  v=pic JPEG  b=pic PNG  n=pic DNG  "
              "m=ALL    u : burst    y : EV bracket")

    cv2.putText(bar, line1a, (8, dy),     cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 255, 0),     fw, cv2.LINE_AA)
    cv2.putText(bar, line1b, (8, dy * 2), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 220, 0),     fw, cv2.LINE_AA)
    cv2.putText(bar, line2a, (8, dy * 3), cv2.FONT_HERSHEY_SIMPLEX, fs, (160, 160, 160), fw, cv2.LINE_AA)
    cv2.putText(bar, line2b, (8, dy * 4), cv2.FONT_HERSHEY_SIMPLEX, fs, (100, 200, 255), fw, cv2.LINE_AA)
    return bar


# ── Save helpers ───────────────────────────────────────────────────────────

def _read_ae(qbe):
    if qbe.cam is None:
        return None, None
    try:
        meta = qbe.cam.capture_metadata()
        return meta.get("ExposureTime"), meta.get("AnalogueGain")
    except Exception:
        return None, None


def _ae_flags(exp_us, gain):
    if exp_us and gain:
        return " --shutter %d --gain %.3f" % (int(exp_us), float(gain))
    return ""


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


def _rpicam_run(qbe, state, mode_str, out_path, ae="", extra=""):
    """Run rpicam-still and return exit code."""
    cmd = (
        "rpicam-still -n --immediate --camera %d --mode %s "
        "--autofocus-mode manual --lens-position %.2f --ev %.1f%s%s -o %s"
        % (qbe.cam_idx, mode_str, state.lp, state.ev, ae, extra, out_path)
    )
    return os.system(cmd)


def _picamera_still_capture(qbe, state, exp_us=None, gain=None):
    """Open Picamera2 in still config, capture one frame, return numpy array.
    exp_us/gain: lock AE from preview to avoid re-exposure on fresh camera open."""
    size = (9248, 6944) if state.save_full else (4624, 3472)
    cam = Picamera2(qbe.cam_idx)
    try:
        cfg = cam.create_still_configuration(
            main={"size": size, "format": "RGB888"}, buffer_count=1)
        cam.configure(cfg)
        cam.start()
        controls = {"AfMode": 0, "LensPosition": state.lp, "ExposureValue": state.ev}
        if exp_us and gain:
            controls["ExposureTime"] = int(exp_us)
            controls["AnalogueGain"] = float(gain)
        cam.set_controls(controls)
        time.sleep(0.5)
        frame = cam.capture_array()
        return frame
    finally:
        try: cam.stop()
        except Exception: pass
        try: cam.close()
        except Exception: pass


def _picamera_dng_capture(qbe, state, out_path, exp_us=None, gain=None):
    """Open Picamera2 with raw + main stream, save DNG file.
    main stream is required by Picamera2 for DNG output alongside raw."""
    size = (9248, 6944) if state.save_full else (4624, 3472)
    cam = Picamera2(qbe.cam_idx)
    try:
        cfg = cam.create_still_configuration(
            main={"size": size, "format": "RGB888"},
            raw={"size": cam.sensor_resolution},
            buffer_count=1)
        cam.configure(cfg)
        cam.start()
        controls = {"AfMode": 0, "LensPosition": state.lp, "ExposureValue": state.ev}
        if exp_us and gain:
            controls["ExposureTime"] = int(exp_us)
            controls["AnalogueGain"] = float(gain)
        cam.set_controls(controls)
        time.sleep(0.5)
        cam.capture_file(out_path)
    finally:
        try: cam.stop()
        except Exception: pass
        try: cam.close()
        except Exception: pass


def _fname(base_ts, tag, ext, state, qbe):
    return "%s_%s_lp%.2f_ev%.1f_cam%d.%s" % (
        base_ts, tag, state.lp, state.ev, qbe.cam_idx, ext)


# ── 6 save routes ──────────────────────────────────────────────────────────

def save_r1(state, qbe, sbe, output_dir, base_ts=None, ae=""):
    """z — rpicam-still → JPEG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "rpi_jpg", "jpg", state, qbe))
    print("[R1] rpi JPEG -> %s" % path)
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, ae)
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r2(state, qbe, sbe, output_dir, base_ts=None, ae=""):
    """x — rpicam-still → PNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "rpi_png", "png", state, qbe))
    print("[R2] rpi PNG  -> %s" % path)
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, ae, " --encoding png")
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r3(state, qbe, sbe, output_dir, base_ts=None, ae=""):
    """c — rpicam-still → DNG + JPEG"""
    t = base_ts or ts()
    # rpicam-still saves .jpg + .dng with same base name
    path = os.path.join(output_dir, _fname(t, "rpi_dng", "jpg", state, qbe))
    print("[R3] rpi DNG  -> %s + .dng" % path)
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, ae, " --raw")
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r4(state, qbe, sbe, output_dir, base_ts=None, frame=None, exp_us=None, gain=None):
    """v — Picamera2 → JPEG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "pic_jpg", "jpg", state, qbe))
    f = frame if frame is not None else _picamera_still_capture(qbe, state, exp_us, gain)
    print("[R4] pic JPEG -> %s" % path)
    cv2.imwrite(path, f, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("     OK")


def save_r5(state, qbe, sbe, output_dir, base_ts=None, frame=None, exp_us=None, gain=None):
    """b — Picamera2 → PNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "pic_png", "png", state, qbe))
    f = frame if frame is not None else _picamera_still_capture(qbe, state, exp_us, gain)
    print("[R5] pic PNG  -> %s" % path)
    cv2.imwrite(path, f)
    print("     OK")


def save_r6(state, qbe, sbe, output_dir, base_ts=None, exp_us=None, gain=None):
    """n — Picamera2 → DNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "pic_dng", "dng", state, qbe))
    print("[R6] pic DNG  -> %s" % path)
    _picamera_dng_capture(qbe, state, path, exp_us, gain)
    print("     OK")


def save_all(state, qbe, sbe, output_dir):
    """m — all 6 routes with shared timestamp and minimal stop/start cycles"""
    os.makedirs(output_dir, exist_ok=True)
    exp_us, gain = _read_ae(qbe) if state.backend == "qtgl" else (None, None)
    ae = _ae_flags(exp_us, gain)
    t  = ts()
    print("[ALL] Starting all 6 routes  ts=%s" % t)

    _stop_for_save(state, qbe, sbe)

    # R1 R2 R3 — rpicam-still (subprocesses, AE locked via --shutter --gain)
    save_r1(state, qbe, sbe, output_dir, t, ae)
    save_r2(state, qbe, sbe, output_dir, t, ae)
    save_r3(state, qbe, sbe, output_dir, t, ae)

    # R4 R5 — Picamera2 JPEG + PNG, share one still capture, AE locked
    frame = _picamera_still_capture(qbe, state, exp_us, gain)
    save_r4(state, qbe, sbe, output_dir, t, frame=frame)
    save_r5(state, qbe, sbe, output_dir, t, frame=frame)

    # R6 — Picamera2 DNG, AE locked
    save_r6(state, qbe, sbe, output_dir, t, exp_us=exp_us, gain=gain)

    _restore_after_save(state, qbe, sbe)
    print("[ALL] Done.")


# ── Burst / EV bracket (rpicam-still JPEG, moved to u/y) ───────────────────

def save_burst(state, qbe, sbe, output_dir, count=5):
    os.makedirs(output_dir, exist_ok=True)
    ae = _ae_flags(*_read_ae(qbe)) if state.backend == "qtgl" else ""
    step    = 0.25
    half    = count // 2
    offsets = [round(-half * step + i * step, 2) for i in range(count)]
    lps     = [round(clamp(state.lp + d, LP_MIN, LP_MAX), 2) for d in offsets]
    print("[BURST] %d shots, LP: %s" % (count, lps))
    _stop_for_save(state, qbe, sbe)
    for lp in lps:
        fname = "%s_%s_lp%.2f_ev%.1f_cam%d.jpg" % (
            ts(), state.save_res_tag(), lp, state.ev, qbe.cam_idx)
        path = os.path.join(output_dir, fname)
        cmd = (
            "rpicam-still -n --immediate --camera %d --mode %s "
            "--autofocus-mode manual --lens-position %.2f --ev %.1f%s -o %s"
            % (qbe.cam_idx, state.save_mode_cmd(), lp, state.ev, ae, path)
        )
        ret = os.system(cmd)
        print("  LP=%.2f -> %s" % (lp, "OK" if ret == 0 else "Error"))
        time.sleep(0.3)
    _restore_after_save(state, qbe, sbe)
    print("[BURST] Done.")


def ev_bracket(state, qbe, sbe, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    offsets = [-1.0, -0.5, 0.0, 0.5, 1.0]
    evs     = [round(clamp(state.ev + d, EV_MIN, EV_MAX), 1) for d in offsets]
    base_ts = ts()
    print("[EV-BRACKET] LP=%.2f, EV values: %s" % (state.lp, evs))
    _stop_for_save(state, qbe, sbe)
    for i, ev in enumerate(evs):
        fname = "%s_%s_lp%.2f_ev%.1f_brk%d_cam%d.jpg" % (
            base_ts, state.save_res_tag(), state.lp, ev, i, qbe.cam_idx)
        path = os.path.join(output_dir, fname)
        cmd = (
            "rpicam-still -n --immediate --camera %d --mode %s "
            "--autofocus-mode manual --lens-position %.2f --ev %.1f -o %s"
            % (qbe.cam_idx, state.save_mode_cmd(), state.lp, ev, path)
        )
        ret = os.system(cmd)
        print("  EV=%+.1f -> %s" % (ev, "OK" if ret == 0 else "Error"))
        time.sleep(0.3)
    _restore_after_save(state, qbe, sbe)
    print("[EV-BRACKET] Done.")


# ── Autofocus ──────────────────────────────────────────────────────────────

def autofocus_once(state, qbe, sbe):
    print("[AF] One-shot autofocus (up to 8s)...")

    def _do_af(cam):
        cam.set_controls({"AfMode": 1, "AfRange": 2, "AfTrigger": 0})
        af_state = 0
        for i in range(80):
            time.sleep(0.1)
            md = cam.capture_metadata()
            af_state = md.get("AfState", 0)
            if i % 10 == 0:
                print("[AF] Scanning... %.1fs" % (i * 0.1), flush=True)
            if af_state in (2, 3):
                break
        md = cam.capture_metadata()
        return md.get("LensPosition"), md.get("AfState", af_state)

    if state.backend == "qtgl":
        if not qbe.alive():
            print("[AF] Camera not running.")
            return
        lp, af_state = _do_af(qbe.cam)
        if lp is not None:
            state.lp = round(clamp(float(lp), LP_MIN, LP_MAX), 2)
        qbe.cam.set_controls({"AfMode": 0, "LensPosition": state.lp})
        print("[AF] %s  LP=%.2f" % ("OK" if af_state == 2 else "Failed", state.lp))
    else:
        sbe.stop()
        time.sleep(0.3)
        p = None
        try:
            p = Picamera2(qbe.cam_idx)
            cfg = p.create_preview_configuration(main={"size": (1280, 720)})
            p.configure(cfg); p.start()
            time.sleep(1.0)
            lp, af_state = _do_af(p)
            if lp is not None:
                state.lp = round(clamp(float(lp), LP_MIN, LP_MAX), 2)
            print("[AF] %s  LP=%.2f" % ("OK" if af_state == 2 else "Failed", state.lp))
        except Exception as e:
            print("[AF] Error: %s" % e)
        finally:
            if p is not None:
                try: p.stop()
                except Exception: pass
                try: p.close()
                except Exception: pass
        time.sleep(0.3)
        sbe.start(state)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    global LP_MAX

    cam_idx    = CAM_IDX
    output_dir = os.path.join(OUTPUT_DIR_BASE, "cam%d" % cam_idx)
    os.makedirs(output_dir, exist_ok=True)

    state    = State()
    state.lp = INIT_LP

    qbe = PicaBackend(cam_idx)
    sbe = StillBackend(cam_idx)

    print("[INFO] Starting Picamera2 preview for cam%d ..." % cam_idx)
    qbe.start(state)
    state.lp = min(state.lp, LP_MAX)
    print("[INFO] LP range: %.2f - %.2f" % (LP_MIN, LP_MAX))
    print("[INFO] Output: %s" % output_dir)

    cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)

    try:
        while True:
            # ── Build display ──────────────────────────────────────────────
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
                bar = make_status_bar(state, cam_idx, LP_MAX, qbe.capture_sz)
                cv2.imshow(WIN_NAME, np.vstack([bar, frame]))

            # ── Keyboard ───────────────────────────────────────────────────
            key = cv2.waitKey(10)
            if key == -1:
                continue
            k = key & 0xFF

            if k == ord('q'):
                break

            # ── Focus ──────────────────────────────────────────────────────
            elif k == ord('='):
                state.lp = round(clamp(state.lp + 0.1, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif k == ord('-'):
                state.lp = round(clamp(state.lp - 0.1, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif k == ord(']'):
                state.lp = round(clamp(state.lp + 0.5, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif k == ord('['):
                state.lp = round(clamp(state.lp - 0.5, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif k == ord('.'):
                state.lp = round(clamp(state.lp + 1.0, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif k == ord(','):
                state.lp = round(clamp(state.lp - 1.0, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

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
                    new_sz = (9248, 6944) if qbe.capture_sz == (4624, 3472) else (4624, 3472)
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
                    "9248x6944 FULL" if state.save_full else "4624x3472 HALF"))

            # ── Autofocus (t) ──────────────────────────────────────────────
            elif k in (ord('t'), ord('T')):
                autofocus_once(state, qbe, sbe)

            # ── 6 save routes (z x c v b n) ───────────────────────────────
            elif k in (ord('z'), ord('Z')):
                os.makedirs(output_dir, exist_ok=True)
                ae = _ae_flags(*_read_ae(qbe)) if state.backend == "qtgl" else ""
                _stop_for_save(state, qbe, sbe)
                save_r1(state, qbe, sbe, output_dir, ae=ae)
                _restore_after_save(state, qbe, sbe)

            elif k in (ord('x'), ord('X')):
                os.makedirs(output_dir, exist_ok=True)
                ae = _ae_flags(*_read_ae(qbe)) if state.backend == "qtgl" else ""
                _stop_for_save(state, qbe, sbe)
                save_r2(state, qbe, sbe, output_dir, ae=ae)
                _restore_after_save(state, qbe, sbe)

            elif k in (ord('c'), ord('C')):
                os.makedirs(output_dir, exist_ok=True)
                ae = _ae_flags(*_read_ae(qbe)) if state.backend == "qtgl" else ""
                _stop_for_save(state, qbe, sbe)
                save_r3(state, qbe, sbe, output_dir, ae=ae)
                _restore_after_save(state, qbe, sbe)

            elif k in (ord('v'), ord('V')):
                os.makedirs(output_dir, exist_ok=True)
                exp_us, gain = _read_ae(qbe) if state.backend == "qtgl" else (None, None)
                _stop_for_save(state, qbe, sbe)
                frame = _picamera_still_capture(qbe, state, exp_us, gain)
                save_r4(state, qbe, sbe, output_dir, frame=frame)
                _restore_after_save(state, qbe, sbe)

            elif k in (ord('b'), ord('B')):
                os.makedirs(output_dir, exist_ok=True)
                exp_us, gain = _read_ae(qbe) if state.backend == "qtgl" else (None, None)
                _stop_for_save(state, qbe, sbe)
                frame = _picamera_still_capture(qbe, state, exp_us, gain)
                save_r5(state, qbe, sbe, output_dir, frame=frame)
                _restore_after_save(state, qbe, sbe)

            elif k in (ord('n'), ord('N')):
                os.makedirs(output_dir, exist_ok=True)
                exp_us, gain = _read_ae(qbe) if state.backend == "qtgl" else (None, None)
                _stop_for_save(state, qbe, sbe)
                save_r6(state, qbe, sbe, output_dir, exp_us=exp_us, gain=gain)
                _restore_after_save(state, qbe, sbe)

            # ── All routes (m) ─────────────────────────────────────────────
            elif k in (ord('m'), ord('M')):
                save_all(state, qbe, sbe, output_dir)

            # ── Burst LP sweep (u) ─────────────────────────────────────────
            elif k in (ord('u'), ord('U')):
                save_burst(state, qbe, sbe, output_dir)

            # ── EV bracket (y) ─────────────────────────────────────────────
            elif k in (ord('y'), ord('Y')):
                ev_bracket(state, qbe, sbe, output_dir)

            # ── Info (f) ───────────────────────────────────────────────────
            elif k in (ord('f'), ord('F')):
                roi = state.roi()
                print("[INFO] cam%d  backend=%s  LP=%.2f  EV=%+.1f  "
                      "Zoom=%dx  Center=(%.2f,%.2f)  "
                      "ROI=(%.3f,%.3f,%.3f,%.3f)  save=%s" % (
                    cam_idx, state.backend, state.lp, state.ev,
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
