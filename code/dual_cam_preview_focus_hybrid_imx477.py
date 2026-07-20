#!/usr/bin/env python3
# Dual-camera hybrid preview + focus control for IMX477 (Raspberry Pi HQ Camera) x2.
# Adapted from preview_focus_hybrid_imx477.py: runs cam0 and cam1 simultaneously in
# a single OpenCV window (side-by-side). Tab switches which camera the keyboard
# commands act on. Save keys: lowercase = active camera only, UPPERCASE = both cameras.
# No LP / AF controls — manual lens only.
#
# Usage: python3 dual_cam_preview_focus_hybrid_imx477.py

import os
import time
import subprocess
from datetime import datetime
from picamera2 import Picamera2
import cv2
import numpy as np

# ── Display ────────────────────────────────────────────────────────────────
STATUS_H  = 145      # pixels for status bar above each camera's image
DISPLAY_W = 960      # per-camera panel width (two panels side by side)
DISPLAY_H = 540
WIN_NAME  = "dual preview"

# rpicam-still subprocess window geometry (still backend only) — cam1 offset
# to the right of cam0 so the two subprocess preview windows don't overlap.
RPICAM_WIN_Y = 50
RPICAM_WIN_W = DISPLAY_W
RPICAM_WIN_H = DISPLAY_H
RPICAM_WIN_X = [100, 100 + DISPLAY_W + 40]

# ── Camera / sensor (IMX477) ───────────────────────────────────────────────
EV_MIN   = -4.0
EV_MAX   =  4.0
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
            print("[WARN] cam%d grab_frame error: %s" % (self.cam_idx, e))
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
        win_x = RPICAM_WIN_X[self.cam_idx]
        return [
            "rpicam-still", "-t", "0",
            "--camera", str(self.cam_idx),
            "--mode", HALF_MODE,
            "--preview", "%d,%d,%d,%d" % (win_x, RPICAM_WIN_Y, RPICAM_WIN_W, RPICAM_WIN_H),
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
            print("[WARN] cam%d rpicam-still died, restarting..." % self.cam_idx)
            self.restart(state)
        if self._pending and (time.time() - self._last_t > DEBOUNCE_S):
            self.restart(state)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


# ── Status bar / panel ─────────────────────────────────────────────────────

def make_status_bar(state, cam_idx, capture_sz, is_active):
    bar = np.zeros((STATUS_H, DISPLAY_W, 3), dtype=np.uint8)
    fs = 0.5
    fw = 2
    dy = 34

    tag = "ACTIVE" if is_active else "  -   "
    line1a = (
        "cam%d [%s] [%s] EV=%+.1f Zoom=%dx"
        % (cam_idx, tag, state.backend.upper(), state.ev, state.zoom)
    )
    line1b = (
        "Save=%s  %dx%d  Center=(%.2f,%.2f)"
        % ("FULL" if state.save_full else "HALF",
           capture_sz[0], capture_sz[1],
           state.zoom_cx, state.zoom_cy)
    )
    line2a = "Tab:switch  e/w:EV a/d:zoom ijkl:pan r:reset p:cap g:save h:bknd f:info q:quit"
    line2b = "lower=active UPPER=BOTH: z/Z x/X c/C v/V b/B n/N m/M y/Y"

    color1 = (0, 255, 0) if is_active else (0, 160, 0)
    cv2.putText(bar, line1a, (8, dy),     cv2.FONT_HERSHEY_SIMPLEX, fs,   color1,          fw, cv2.LINE_AA)
    cv2.putText(bar, line1b, (8, dy * 2), cv2.FONT_HERSHEY_SIMPLEX, fs,   (0, 200, 0),     fw, cv2.LINE_AA)
    cv2.putText(bar, line2a, (8, dy * 3), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1, cv2.LINE_AA)
    cv2.putText(bar, line2b, (8, dy * 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100, 200, 255), 1, cv2.LINE_AA)
    return bar


def make_panel(frame, state, cam_idx, capture_sz, is_active):
    if frame is None:
        frame = np.zeros((DISPLAY_H, DISPLAY_W, 3), dtype=np.uint8)
    bar = make_status_bar(state, cam_idx, capture_sz, is_active)
    panel = np.vstack([bar, frame])
    if is_active:
        cv2.rectangle(panel, (0, 0), (panel.shape[1] - 1, panel.shape[0] - 1),
                       (0, 165, 255), 4)
    return panel


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
    print("[R1] cam%d rpi JPEG -> %s" % (qbe.cam_idx, path))
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path)
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r2(state, qbe, output_dir, base_ts=None):
    """x — rpicam-still → PNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "x_r2_rpicam_png", "png", state, qbe))
    print("[R2] cam%d rpi PNG  -> %s" % (qbe.cam_idx, path))
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, " --encoding png")
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r3(state, qbe, output_dir, base_ts=None):
    """c — rpicam-still → DNG + JPEG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "c_r3_rpicam_dng", "jpg", state, qbe))
    print("[R3] cam%d rpi DNG  -> %s + .dng" % (qbe.cam_idx, path))
    ret = _rpicam_run(qbe, state, state.save_mode_cmd(), path, " --raw")
    print("     %s" % ("OK" if ret == 0 else "Error %d" % ret))


def save_r4(state, qbe, output_dir, base_ts=None):
    """v — Picamera2 → JPEG (from running preview)"""
    if qbe.cam is None:
        print("[R4] cam%d Skip: preview not running" % qbe.cam_idx); return
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "v_r4_picam_jpg", "jpg", state, qbe))
    frame = qbe.cam.capture_array()
    print("[R4] cam%d pic JPEG -> %s" % (qbe.cam_idx, path))
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("     OK")


def save_r5(state, qbe, output_dir, base_ts=None):
    """b — Picamera2 → PNG (from running preview)"""
    if qbe.cam is None:
        print("[R5] cam%d Skip: preview not running" % qbe.cam_idx); return
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "b_r5_picam_png", "png", state, qbe))
    frame = qbe.cam.capture_array()
    print("[R5] cam%d pic PNG  -> %s" % (qbe.cam_idx, path))
    cv2.imwrite(path, frame)
    print("     OK")


def save_r6(state, qbe, output_dir, base_ts=None):
    """n — Picamera2 → DNG"""
    t = base_ts or ts()
    path = os.path.join(output_dir, _fname(t, "n_r6_picam_dng", "dng", state, qbe))
    print("[R6] cam%d pic DNG  -> %s" % (qbe.cam_idx, path))
    _picamera_dng_capture(qbe, state, path)
    print("     OK")


def save_all(state, qbe, sbe, output_dir):
    """m — all 6 routes. R4/R5 from running preview, then stop for R1~R3/R6."""
    os.makedirs(output_dir, exist_ok=True)
    t = ts()
    print("[ALL] cam%d starting all 6 routes  ts=%s" % (qbe.cam_idx, t))

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
    print("[ALL] cam%d Done." % qbe.cam_idx)


# ── EV bracket ─────────────────────────────────────────────────────────────

def ev_bracket(state, qbe, sbe, output_dir):
    """Picamera2 EV bracket: 5 shots sweeping EV."""
    os.makedirs(output_dir, exist_ok=True)
    offsets = [-1.0, -0.5, 0.0, 0.5, 1.0]
    evs     = [round(clamp(state.ev + d, EV_MIN, EV_MAX), 1) for d in offsets]
    print("[EV-BRACKET] cam%d EV: %s  (Picamera2 PNG)" % (qbe.cam_idx, evs))

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
                print("  cam%d EV=%+.1f -> OK" % (qbe.cam_idx, ev))
        finally:
            try: cam.stop()
            except Exception: pass
            try: cam.close()
            except Exception: pass
    finally:
        _restore_after_save(state, qbe, sbe)
    print("[EV-BRACKET] cam%d Done." % qbe.cam_idx)


# ── Main ───────────────────────────────────────────────────────────────────

def print_controls():
    print()
    print("=" * 70)
    print("  DUAL IMX477 PREVIEW  (Tab = switch active camera)")
    print("=" * 70)
    print("  Exposure: e/w   Zoom: a/d   Pan: ijkl   Reset zoom: r")
    print("  ROI presets: ` 1-0     Preview cap res: p     Save res: g")
    print("  Backend (active cam only): h")
    print("  Save routes — lower=active camera, UPPER=BOTH cameras:")
    print("    z/Z rpi-jpg  x/X rpi-png  c/C rpi-dng  v/V pic-jpg")
    print("    b/B pic-png  n/N pic-dng  m/M ALL-6    y/Y ev-bracket")
    print("  f: print info    q: quit")
    print("=" * 70)
    print()


def main():
    cam_indices = (0, 1)
    output_dirs = [os.path.join(OUTPUT_DIR_BASE, "cam%d" % i) for i in cam_indices]
    for od in output_dirs:
        os.makedirs(od, exist_ok=True)

    states = [State(), State()]
    qbes   = [PicaBackend(0), PicaBackend(1)]
    sbes   = [StillBackend(0), StillBackend(1)]

    print("[INFO] Starting Picamera2 preview for cam0 and cam1 (IMX477 manual lens)...")
    for i in cam_indices:
        qbes[i].start(states[i])
    print("[INFO] Output: %s" % OUTPUT_DIR_BASE)
    print_controls()

    active = 0
    cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)

    SAVE_ROUTES = {
        'z': (save_r1, True),
        'x': (save_r2, True),
        'c': (save_r3, True),
        'v': (save_r4, False),
        'b': (save_r5, False),
        'n': (save_r6, True),
    }

    def run_route(letter, idx, base_ts=None):
        fn, needs_stop = SAVE_ROUTES[letter]
        od = output_dirs[idx]
        os.makedirs(od, exist_ok=True)
        if needs_stop:
            _stop_for_save(states[idx], qbes[idx], sbes[idx])
            try:
                fn(states[idx], qbes[idx], od, base_ts)
            finally:
                _restore_after_save(states[idx], qbes[idx], sbes[idx])
        else:
            fn(states[idx], qbes[idx], od, base_ts)

    def run_route_targets(letter, both):
        targets = cam_indices if both else (active,)
        base_ts = ts() if both else None
        for idx in targets:
            run_route(letter, idx, base_ts)

    def run_all_targets(both):
        for idx in (cam_indices if both else (active,)):
            save_all(states[idx], qbes[idx], sbes[idx], output_dirs[idx])

    def run_ev_bracket_targets(both):
        for idx in (cam_indices if both else (active,)):
            ev_bracket(states[idx], qbes[idx], sbes[idx], output_dirs[idx])

    try:
        while True:
            panels = []
            for i in cam_indices:
                st = states[i]
                if st.backend == "qtgl":
                    frame = qbes[i].grab_frame()
                else:
                    sbes[i].tick(st)
                    frame = np.zeros((DISPLAY_H, DISPLAY_W, 3), dtype=np.uint8)
                    cv2.putText(frame, "rpicam-still active (separate window)",
                                (16, DISPLAY_H // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 200, 80), 2, cv2.LINE_AA)
                panels.append(make_panel(frame, st, i, qbes[i].capture_sz, i == active))

            cv2.imshow(WIN_NAME, np.hstack(panels))

            key = cv2.waitKey(10)
            if key == -1:
                continue
            k = key & 0xFF

            st  = states[active]
            qbe = qbes[active]
            sbe = sbes[active]

            if k == ord('q'):
                break

            # ── Switch active camera ──────────────────────────────────────
            elif k == 9:  # Tab
                active = 1 - active
                print("[ACTIVE] cam%d" % active)

            # ── EV ─────────────────────────────────────────────────────────
            elif k in (ord('e'), ord('E')):
                st.ev = round(clamp(st.ev + 0.5, EV_MIN, EV_MAX), 1)
                if st.backend == "qtgl": qbe.apply_controls(st)
                else: sbe.mark_dirty()

            elif k in (ord('w'), ord('W')):
                st.ev = round(clamp(st.ev - 0.5, EV_MIN, EV_MAX), 1)
                if st.backend == "qtgl": qbe.apply_controls(st)
                else: sbe.mark_dirty()

            # ── Zoom: a=out, d=in ──────────────────────────────────────────
            elif k in (ord('a'), ord('A')):
                st.zoom = clamp(st.zoom - 1, ZOOM_MIN, ZOOM_MAX)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            elif k in (ord('d'), ord('D')):
                st.zoom = clamp(st.zoom + 1, ZOOM_MIN, ZOOM_MAX)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            # ── Pan: ijkl ─────────────────────────────────────────────────
            elif k == ord('i'):
                _ps = 0.1 / max(st.zoom, 1)
                st.zoom_cy = clamp(st.zoom_cy - _ps, 0.05, 0.95)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            elif k == ord('k'):
                _ps = 0.1 / max(st.zoom, 1)
                st.zoom_cy = clamp(st.zoom_cy + _ps, 0.05, 0.95)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            elif k == ord('j'):
                _ps = 0.1 / max(st.zoom, 1)
                st.zoom_cx = clamp(st.zoom_cx - _ps, 0.05, 0.95)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            elif k == ord('l'):
                _ps = 0.1 / max(st.zoom, 1)
                st.zoom_cx = clamp(st.zoom_cx + _ps, 0.05, 0.95)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            # ── Reset zoom ─────────────────────────────────────────────────
            elif k in (ord('r'), ord('R')):
                st.zoom, st.zoom_cx, st.zoom_cy = 1, 0.5, 0.5
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            # ── ROI presets ────────────────────────────────────────────────
            elif k in ROI_PRESETS:
                cx, cy, w, h = ROI_PRESETS[k]
                st.zoom_cx, st.zoom_cy = cx, cy
                st.zoom = clamp(int(round(1.0 / w)) if w < 1.0 else 1,
                                 ZOOM_MIN, ZOOM_MAX)
                if st.backend == "qtgl": qbe.apply_zoom(st)
                else: sbe.mark_dirty()

            # ── Preview capture resolution (p) ─────────────────────────────
            elif k in (ord('p'), ord('P')):
                if st.backend == "qtgl":
                    new_sz = CAPTURE_FULL if qbe.capture_sz == CAPTURE_HALF else CAPTURE_HALF
                    print("[CAP] cam%d switching preview capture to %dx%d ..." % (active, new_sz[0], new_sz[1]))
                    qbe.switch_capture_sz(new_sz, st)
                    print("[CAP] Done.")

            # ── Backend toggle (h) — active camera only, independent ──────
            elif k in (ord('h'), ord('H')):
                if st.backend == "qtgl":
                    print("[MODE] cam%d switching to rpicam-still preview..." % active)
                    qbe.stop()
                    st.backend = "still"
                    sbe.start(st)
                    print("[MODE] cam%d rpicam-still active." % active)
                else:
                    print("[MODE] cam%d switching to Picamera2 preview..." % active)
                    sbe.stop()
                    time.sleep(0.3)
                    st.backend = "qtgl"
                    qbe.start(st)
                    print("[MODE] cam%d Picamera2 active." % active)

            # ── Save resolution toggle (g) ─────────────────────────────────
            elif k in (ord('g'), ord('G')):
                st.save_full = not st.save_full
                print("[RES] cam%d save: %s" % (
                    active, "4056x3040 FULL" if st.save_full else "2028x1520 HALF"))

            # ── Save routes: lower=active, UPPER=both ──────────────────────
            elif k == ord('z'): run_route_targets('z', both=False)
            elif k == ord('Z'): run_route_targets('z', both=True)
            elif k == ord('x'): run_route_targets('x', both=False)
            elif k == ord('X'): run_route_targets('x', both=True)
            elif k == ord('c'): run_route_targets('c', both=False)
            elif k == ord('C'): run_route_targets('c', both=True)
            elif k == ord('v'): run_route_targets('v', both=False)
            elif k == ord('V'): run_route_targets('v', both=True)
            elif k == ord('b'): run_route_targets('b', both=False)
            elif k == ord('B'): run_route_targets('b', both=True)
            elif k == ord('n'): run_route_targets('n', both=False)
            elif k == ord('N'): run_route_targets('n', both=True)

            # ── All routes (m = active, M = both) ──────────────────────────
            elif k == ord('m'): run_all_targets(both=False)
            elif k == ord('M'): run_all_targets(both=True)

            # ── EV bracket (y = active, Y = both) ──────────────────────────
            elif k == ord('y'): run_ev_bracket_targets(both=False)
            elif k == ord('Y'): run_ev_bracket_targets(both=True)

            # ── Info (f) ───────────────────────────────────────────────────
            elif k in (ord('f'), ord('F')):
                roi = st.roi()
                print("[INFO] active=cam%d backend=%s EV=%+.1f Zoom=%dx "
                      "Center=(%.2f,%.2f) ROI=(%.3f,%.3f,%.3f,%.3f) save=%s" % (
                    active, st.backend, st.ev, st.zoom, st.zoom_cx, st.zoom_cy,
                    roi[0], roi[1], roi[2], roi[3],
                    "FULL" if st.save_full else "HALF"))

    except KeyboardInterrupt:
        pass
    finally:
        for i in cam_indices:
            if states[i].backend == "qtgl":
                qbes[i].stop()
            else:
                sbes[i].stop()
        cv2.destroyAllWindows()
        print("[INFO] Exited.")


if __name__ == "__main__":
    main()
