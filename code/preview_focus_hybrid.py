#!/usr/bin/env python3
# Hybrid preview + focus control for Arducam 64MP (OV64A40) — Rpi5 dual-cam version.
# Adapted from ../64mp/cam_test/preview_focus_hybrid.py
# Changes vs original:
#   - --camera 0|1  selects which physical camera to use
#   - OUTPUT_DIR moved to ~/Desktop/images/preview_captures/
#   - Rpi5: no OOM concern, but saves still use rpicam-still for quality comparison
#
# Default: Picamera2 QTGL preview (correct colors, instant LP/EV via set_controls).
# Press V to switch to rpicam-still preview (also correct colors, needs debounce restart).
# All captures use rpicam-still subprocess.
#
# LensPosition: 0.0 = infinity, higher = closer (~9-10cm at max)
#
# Usage: python3 preview_focus_hybrid.py [--camera 0|1]

import os
import sys
import time
import select
import termios
import tty
import subprocess
import argparse
from datetime import datetime
from picamera2 import Picamera2, Preview

# -- Config --
PREVIEW_X = 100
PREVIEW_Y = 50
PREVIEW_W = 1280
PREVIEW_H = 720

LORES_W = PREVIEW_W
LORES_H = PREVIEW_H

INIT_LP   = 15.0
LP_MIN    = 0.0
LP_USER_MAX = 16.0      # hard cap regardless of what camera reports
LP_MAX    = LP_USER_MAX # updated at startup
EV_MIN    = -4.0
EV_MAX    =  4.0
ZOOM_MIN  = 1
ZOOM_MAX  = 20

SENSOR_W  = 9248
SENSOR_H  = 6944

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
    '`': (0.50, 0.50, 1.00, 1.00),
    '1': (0.50, 0.50, 0.50, 0.50),
    '2': (0.25, 0.25, 0.50, 0.50),
    '3': (0.75, 0.25, 0.50, 0.50),
    '4': (0.25, 0.75, 0.50, 0.50),
    '5': (0.75, 0.75, 0.50, 0.50),
    '6': (0.50, 0.50, 0.25, 0.25),
    '7': (0.25, 0.25, 0.25, 0.25),
    '8': (0.75, 0.25, 0.25, 0.25),
    '9': (0.25, 0.75, 0.25, 0.25),
    '0': (0.75, 0.75, 0.25, 0.25),
}


# -- Helpers --

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_key():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return ''


def _read_lp_max(cam):
    try:
        ctrl = cam.camera_controls.get("LensPosition")
        if ctrl and len(ctrl) >= 2 and ctrl[1] > 0:
            return min(float(ctrl[1]), LP_USER_MAX)
    except Exception:
        pass
    return LP_USER_MAX


# -- State --

class State:
    def __init__(self):
        self.lp        = INIT_LP
        self.ev        = 0.0
        self.zoom      = 1
        self.zoom_cx   = 0.5
        self.zoom_cy   = 0.5
        self.save_full = True       # True=9248x6944, False=4624x3472
        self.backend   = "qtgl"     # "qtgl" or "still"

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


# -- PicaBackend (QTGL) --

class PicaBackend:
    def __init__(self, cam_idx):
        self.cam_idx = cam_idx
        self.cam = None

    def start(self, state):
        global LP_MAX
        self.cam = Picamera2(self.cam_idx)
        LP_MAX = _read_lp_max(self.cam)
        cfg = self.cam.create_preview_configuration(
            main={"size": (4624, 3472)},
            lores={"size": (LORES_W, LORES_H)},
            display="lores",
        )
        self.cam.configure(cfg)
        self.cam.start_preview(Preview.QTGL)
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
            try:
                self.cam.stop_preview()
            except Exception:
                pass
            try:
                self.cam.stop()
            except Exception:
                pass
            try:
                self.cam.close()
            except Exception:
                pass
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

    def alive(self):
        return self.cam is not None


# -- StillBackend (rpicam-still) --

class StillBackend:
    def __init__(self, cam_idx):
        self.cam_idx  = cam_idx
        self.proc     = None
        self._pending = False
        self._last_t  = 0.0

    def _cmd(self, state):
        x, y, w, h = state.roi()
        return [
            "rpicam-still",
            "-t", "0",
            "--camera", str(self.cam_idx),
            "--mode", "4624:3472:12:P",
            "--preview", "%d,%d,%d,%d" % (PREVIEW_X, PREVIEW_Y, PREVIEW_W, PREVIEW_H),
            "--info-text", INFO_FMT,
            "--autofocus-mode", "manual",
            "--lens-position", "%.2f" % state.lp,
            "--ev", "%.1f" % state.ev,
            "--roi", "%.4f,%.4f,%.4f,%.4f" % (x, y, w, h),
            "--verbose", "0",
        ]

    def start(self, state):
        self.proc = subprocess.Popen(
            self._cmd(state),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
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
            print("\n[WARN] rpicam-still died, restarting...")
            self.restart(state)
        if self._pending and (time.time() - self._last_t > DEBOUNCE_S):
            self.restart(state)
            print("\rLP=%.2f  EV=%+.1f  Zoom=%dx" % (
                state.lp, state.ev, state.zoom), end="", flush=True)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


# -- Save helpers --

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


def save_single(state, qbe, sbe, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    fname = "%s_%s_lp%.2f_ev%.1f_cam%d.jpg" % (
        ts(), state.save_res_tag(), state.lp, state.ev, qbe.cam_idx)
    path  = os.path.join(output_dir, fname)
    _stop_for_save(state, qbe, sbe)
    cmd = (
        "rpicam-still -n --immediate --camera %d --mode %s "
        "--autofocus-mode manual --lens-position %.2f --ev %.1f -o %s"
        % (qbe.cam_idx, state.save_mode_cmd(), state.lp, state.ev, path)
    )
    print("\n[SAVE] %s  LP=%.2f  EV=%.1f ..." % (
        "9248x6944" if state.save_full else "4624x3472", state.lp, state.ev))
    ret = os.system(cmd)
    time.sleep(0.15)
    _restore_after_save(state, qbe, sbe)
    if ret == 0:
        print("[SAVE] Done: %s" % path)
    else:
        print("[SAVE] Error (exit %d)" % ret)


def save_burst(state, qbe, sbe, output_dir, count=5):
    os.makedirs(output_dir, exist_ok=True)
    step    = 0.25
    half    = count // 2
    offsets = [round(-half * step + i * step, 2) for i in range(count)]
    lps     = [round(clamp(state.lp + d, LP_MIN, LP_MAX), 2) for d in offsets]
    print("\n[BURST] %d shots, LP: %s" % (count, lps))
    _stop_for_save(state, qbe, sbe)
    for lp in lps:
        fname = "%s_%s_lp%.2f_ev%.1f_cam%d.jpg" % (
            ts(), state.save_res_tag(), lp, state.ev, qbe.cam_idx)
        path  = os.path.join(output_dir, fname)
        cmd = (
            "rpicam-still -n --immediate --camera %d --mode %s "
            "--autofocus-mode manual --lens-position %.2f --ev %.1f -o %s"
            % (qbe.cam_idx, state.save_mode_cmd(), lp, state.ev, path)
        )
        ret = os.system(cmd)
        if ret == 0:
            print("  LP=%.2f -> %s" % (lp, fname))
        else:
            print("  LP=%.2f -> Error (exit %d)" % (lp, ret))
        time.sleep(0.3)
    _restore_after_save(state, qbe, sbe)
    print("[BURST] Done. Output: %s" % output_dir)


def ev_bracket(state, qbe, sbe, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    offsets = [-1.0, -0.5, 0.0, 0.5, 1.0]
    evs     = [round(clamp(state.ev + d, EV_MIN, EV_MAX), 1) for d in offsets]
    base_ts = ts()
    print("\n[EV-BRACKET] LP=%.2f, EV values: %s" % (state.lp, evs))
    _stop_for_save(state, qbe, sbe)
    for i, ev in enumerate(evs):
        fname = "%s_%s_lp%.2f_ev%.1f_brk%d_cam%d.jpg" % (
            base_ts, state.save_res_tag(), state.lp, ev, i, qbe.cam_idx)
        path  = os.path.join(output_dir, fname)
        cmd = (
            "rpicam-still -n --immediate --camera %d --mode %s "
            "--autofocus-mode manual --lens-position %.2f --ev %.1f -o %s"
            % (qbe.cam_idx, state.save_mode_cmd(), state.lp, ev, path)
        )
        ret = os.system(cmd)
        if ret == 0:
            print("  EV=%+.1f -> %s" % (ev, fname))
        else:
            print("  EV=%+.1f -> Error (exit %d)" % (ev, ret))
        time.sleep(0.3)
    _restore_after_save(state, qbe, sbe)
    print("[EV-BRACKET] Done. Output: %s" % output_dir)


# -- Autofocus --

def autofocus_once(state, qbe, sbe):
    """One-shot AF: trigger, wait for result, lock LP, return to manual."""
    print("\n[AF] One-shot autofocus (up to 8s)...")

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
        lp = md.get("LensPosition")
        af_state = md.get("AfState", af_state)
        return lp, af_state

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
            p.configure(cfg)
            p.start()
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


# -- Print controls --

def print_controls(lp_max, cam_idx):
    print()
    print("=" * 62)
    print("  HYBRID PREVIEW  cam%d  (default: Picamera2 QTGL)" % cam_idx)
    print("  LP: 0.0=infinity  %.2f=closest (~9-10cm)" % lp_max)
    print("=" * 62)
    print("  Focus")
    print("    = / -         LP +0.1 / -0.1  (fine)")
    print("    ] / [         LP +0.5 / -0.5  (medium)")
    print("    . / ,         LP +1.0 / -1.0  (coarse)")
    print("    t             one-shot autofocus (lock LP when done)")
    print()
    print("  Exposure")
    print("    e / w         EV +0.5 / -0.5")
    print()
    print("  Zoom  (QTGL: ScalerCrop | still: --roi sensor crop)")
    print("    z / x         zoom in / out  (1x to %dx)" % ZOOM_MAX)
    print("    i / k         pan up / down")
    print("    j / l         pan left / right")
    print("    r             reset zoom 1x, center")
    print()
    print("  ROI presets")
    print("    `             full frame (1x)")
    print("    1-5           2x regions: center / TL / TR / BL / BR")
    print("    6-0           4x regions: center / TL / TR / BL / BR")
    print()
    print("  Capture")
    print("    s             save single (current resolution)")
    print("    b             burst: 5 shots LP +/-0.5")
    print("    n             EV bracket: 5 shots EV -2 to +2 at current LP")
    print()
    print("  Mode")
    print("    v             toggle preview backend (QTGL <-> rpicam-still)")
    print("    m             toggle save resolution (FULL 9248x6944 <-> HALF 4624x3472)")
    print()
    print("    f             print current state to terminal")
    print("    h             print this help")
    print("    q             quit")
    print("=" * 62)
    print("  QTGL: LP/EV/zoom instant.  still: 0.25s debounce restart.")
    print("  still mode shows LP/focus/FPS in preview window title bar.")
    print("=" * 62)
    print()


# -- Main --

def main():
    global LP_MAX

    parser = argparse.ArgumentParser(description="Hybrid preview — Rpi5 dual-cam")
    parser.add_argument("--camera", type=int, default=0, choices=[0, 1],
                        help="Camera index (0 or 1)")
    args = parser.parse_args()

    cam_idx    = args.camera
    output_dir = os.path.join(OUTPUT_DIR_BASE, "cam%d" % cam_idx)
    os.makedirs(output_dir, exist_ok=True)

    state    = State()
    state.lp = INIT_LP

    qbe = PicaBackend(cam_idx)
    sbe = StillBackend(cam_idx)

    print("[INFO] Starting QTGL preview for cam%d ..." % cam_idx)
    qbe.start(state)
    state.lp = min(state.lp, LP_MAX)
    print("[INFO] LP range: %.2f - %.2f" % (LP_MIN, LP_MAX))
    print_controls(LP_MAX, cam_idx)
    print("[INFO] QTGL preview active.  Output: %s" % output_dir)
    print()

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    try:
        while True:
            if state.backend == "still":
                sbe.tick(state)

            key = read_key()
            if not key:
                time.sleep(0.02)
                continue

            # -- Quit
            if key in ('q', 'Q'):
                break

            # -- Focus
            elif key == '=':
                state.lp = round(clamp(state.lp + 0.1, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif key == '-':
                state.lp = round(clamp(state.lp - 0.1, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif key == ']':
                state.lp = round(clamp(state.lp + 0.5, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif key == '[':
                state.lp = round(clamp(state.lp - 0.5, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif key == '.':
                state.lp = round(clamp(state.lp + 1.0, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif key == ',':
                state.lp = round(clamp(state.lp - 1.0, LP_MIN, LP_MAX), 2)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            # -- EV
            elif key in ('e', 'E'):
                state.ev = round(clamp(state.ev + 0.5, EV_MIN, EV_MAX), 1)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            elif key in ('w', 'W'):
                state.ev = round(clamp(state.ev - 0.5, EV_MIN, EV_MAX), 1)
                if state.backend == "qtgl": qbe.apply_controls(state)
                else: sbe.mark_dirty()

            # -- Zoom
            elif key in ('z', 'Z'):
                state.zoom = clamp(state.zoom + 1, ZOOM_MIN, ZOOM_MAX)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif key in ('x', 'X'):
                state.zoom = clamp(state.zoom - 1, ZOOM_MIN, ZOOM_MAX)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # -- Pan (step scales with zoom: ~10% of visible area per keypress)
            elif key == 'i':
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cy = clamp(state.zoom_cy - _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif key == 'k':
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cy = clamp(state.zoom_cy + _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif key == 'j':
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cx = clamp(state.zoom_cx - _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            elif key == 'l':
                _ps = 0.1 / max(state.zoom, 1)
                state.zoom_cx = clamp(state.zoom_cx + _ps, 0.05, 0.95)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # -- Reset zoom
            elif key in ('r', 'R'):
                state.zoom    = 1
                state.zoom_cx = 0.5
                state.zoom_cy = 0.5
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # -- ROI presets
            elif key in ROI_PRESETS:
                cx, cy, w, h = ROI_PRESETS[key]
                state.zoom_cx = cx
                state.zoom_cy = cy
                state.zoom    = clamp(int(round(1.0 / w)) if w < 1.0 else 1, ZOOM_MIN, ZOOM_MAX)
                if state.backend == "qtgl": qbe.apply_zoom(state)
                else: sbe.mark_dirty()

            # -- Toggle preview backend
            elif key in ('v', 'V'):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                if state.backend == "qtgl":
                    print("\n[MODE] Switching to rpicam-still preview...")
                    qbe.stop()
                    state.backend = "still"
                    sbe.start(state)
                    print("[MODE] rpicam-still active. LP/focus shown in window title.")
                else:
                    print("\n[MODE] Switching to Picamera2 QTGL preview...")
                    sbe.stop()
                    time.sleep(0.3)
                    state.backend = "qtgl"
                    qbe.start(state)
                    print("[MODE] QTGL active.")
                tty.setcbreak(fd)

            # -- Toggle save resolution
            elif key in ('m', 'M'):
                state.save_full = not state.save_full
                print("\n[RES] Save: %s" % (
                    "9248x6944 FULL" if state.save_full else "4624x3472 HALF"))

            # -- Autofocus
            elif key in ('t', 'T'):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                autofocus_once(state, qbe, sbe)
                tty.setcbreak(fd)

            # -- Capture
            elif key in ('s', 'S'):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                save_single(state, qbe, sbe, output_dir)
                tty.setcbreak(fd)

            elif key in ('b', 'B'):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                save_burst(state, qbe, sbe, output_dir)
                tty.setcbreak(fd)

            elif key in ('n', 'N'):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                ev_bracket(state, qbe, sbe, output_dir)
                tty.setcbreak(fd)

            # -- Help
            elif key in ('h', 'H'):
                print_controls(LP_MAX, cam_idx)

            # -- Info
            elif key in ('f', 'F'):
                roi = state.roi()
                print("\n[INFO] cam%d  backend=%s  LP=%.2f  EV=%+.1f  "
                      "Zoom=%dx  Center=(%.2f,%.2f)  "
                      "ROI=(%.3f,%.3f,%.3f,%.3f)  save=%s" % (
                    cam_idx, state.backend, state.lp, state.ev,
                    state.zoom, state.zoom_cx, state.zoom_cy,
                    roi[0], roi[1], roi[2], roi[3],
                    "FULL" if state.save_full else "HALF"))

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if state.backend == "qtgl":
            qbe.stop()
        else:
            sbe.stop()
        print("\n[INFO] Exited.")


if __name__ == "__main__":
    main()
