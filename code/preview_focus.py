"""
双摄交互式预览 + 对焦 + 拍照
OpenCV 单窗口左右拼接显示，Picamera2 采集，无 rpicam-still。

键盘控制：
  = / -       LP ±0.1
  ] / [       LP ±0.5
  . / ,       LP ±1.0
  t           单次自动对焦（完成后锁定 LP）
  e / w       EV ±0.5
  z / x       放大 / 缩小（1x–20x）
  i/k/j/l     上/下/左/右平移
  r           重置缩放到 1x 中心
  s           当前相机拍单张
  S           双摄各拍一张（串行）
  b           burst 5 张（LP ±0.25 步）
  n           EV bracket 5 张（±0.5 步）
  m           切换保存分辨率 full ↔ half
  f           打印当前状态
  h           打印帮助
  Tab         切换活动相机
  q           退出
"""

import os
import time
import cv2
import numpy as np
from picamera2 import Picamera2

from cam_common import (
    SENSOR_MODES, LP_MIN, LP_MAX, EV_MIN, EV_MAX,
    SAVE_DIR_BASE, clamp, timestamp,
)

PREVIEW_SIZE = (960, 720)
INIT_LP = 5.0
ZOOM_MIN = 1.0
ZOOM_MAX = 20.0
ZOOM_STEP = 1.2
PAN_STEP = 0.05

SAVE_DIR = os.path.join(SAVE_DIR_BASE, "preview_captures")


class State:
    def __init__(self):
        self.lp = INIT_LP
        self.ev = 0.0
        self.zoom = 1.0
        self.zoom_cx = 0.5
        self.zoom_cy = 0.5
        self.save_full = True  # True=9248x6944, False=4624x3472

    def roi(self):
        """返回 ScalerCrop (x, y, w, h)，基于传感器全分辨率。"""
        sw, sh = SENSOR_MODES["full"]
        fw = int(sw / self.zoom)
        fh = int(sh / self.zoom)
        x = int(clamp(self.zoom_cx * sw - fw / 2, 0, sw - fw))
        y = int(clamp(self.zoom_cy * sh - fh / 2, 0, sh - fh))
        return (x, y, fw, fh)

    def save_size(self):
        return SENSOR_MODES["full"] if self.save_full else SENSOR_MODES["half"]

    def info(self, cam_idx):
        res = "full" if self.save_full else "half"
        return (f"cam{cam_idx}  LP={self.lp:.2f}  EV={self.ev:+.1f}"
                f"  zoom={self.zoom:.1f}x  save={res}")


class DualCam:
    def __init__(self):
        self.states = [State(), State()]
        self.cams = []
        preview_size = SENSOR_MODES["mid"]
        for idx in range(2):
            cam = Picamera2(idx)
            cfg = cam.create_preview_configuration(
                main={"size": preview_size, "format": "BGR888"},
                buffer_count=2,
            )
            cam.configure(cfg)
            cam.start()
            cam.set_controls({
                "AfMode": 0,
                "LensPosition": self.states[idx].lp,
                "ExposureValue": self.states[idx].ev,
            })
            self.cams.append(cam)

    def apply_controls(self, idx):
        st = self.states[idx]
        self.cams[idx].set_controls({
            "AfMode": 0,
            "LensPosition": st.lp,
            "ExposureValue": st.ev,
        })

    def apply_zoom(self, idx):
        self.cams[idx].set_controls({"ScalerCrop": self.states[idx].roi()})

    def grab_frame(self, idx):
        return self.cams[idx].capture_array()

    def capture_full(self, idx):
        st = self.states[idx]
        cam = self.cams[idx]
        cam.stop()
        still_cfg = cam.create_still_configuration(
            main={"size": st.save_size(), "format": "BGR888"},
            buffer_count=1,
        )
        cam.configure(still_cfg)
        cam.start()
        cam.set_controls({"AfMode": 0, "LensPosition": st.lp, "ExposureValue": st.ev})
        time.sleep(0.3)
        frame = cam.capture_array()
        cam.stop()
        # 恢复预览配置
        preview_cfg = cam.create_preview_configuration(
            main={"size": SENSOR_MODES["mid"], "format": "BGR888"},
            buffer_count=2,
        )
        cam.configure(preview_cfg)
        cam.start()
        cam.set_controls({
            "AfMode": 0,
            "LensPosition": st.lp,
            "ExposureValue": st.ev,
        })
        self.apply_zoom(idx)
        return frame

    def autofocus_once(self, idx):
        cam = self.cams[idx]
        print(f"[cam{idx}] 触发自动对焦…")
        cam.set_controls({"AfMode": 1, "AfTrigger": 0})
        deadline = time.time() + 8.0
        while time.time() < deadline:
            meta = cam.capture_metadata()
            if meta.get("AfState") == 2:  # focused
                lp = meta.get("LensPosition", self.states[idx].lp)
                self.states[idx].lp = lp
                cam.set_controls({"AfMode": 0, "LensPosition": lp})
                print(f"[cam{idx}] AF 完成，LP={lp:.3f}")
                return lp
            time.sleep(0.1)
        cam.set_controls({"AfMode": 0, "LensPosition": self.states[idx].lp})
        print(f"[cam{idx}] AF 超时，保持 LP={self.states[idx].lp:.2f}")
        return None

    def close(self):
        for cam in self.cams:
            try:
                cam.stop()
                cam.close()
            except Exception:
                pass


def _save_frame(frame, cam_idx, suffix=""):
    cam_dir = os.path.join(SAVE_DIR, f"cam{cam_idx}")
    os.makedirs(cam_dir, exist_ok=True)
    ts = timestamp()
    path = os.path.join(cam_dir, f"{ts}{suffix}_cam{cam_idx}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"[cam{cam_idx}] 已保存 {path}")
    return path


def save_single(dual, idx):
    frame = dual.capture_full(idx)
    st = dual.states[idx]
    _save_frame(frame, idx, f"_lp{st.lp:.2f}_ev{st.ev:+.1f}")


def save_both(dual):
    for idx in range(2):
        save_single(dual, idx)


def save_burst(dual, idx, count=5):
    st = dual.states[idx]
    base_lp = st.lp
    offsets = [-0.5, -0.25, 0.0, 0.25, 0.5]
    for off in offsets:
        lp = clamp(base_lp + off, LP_MIN, LP_MAX)
        st.lp = lp
        dual.apply_controls(idx)
        time.sleep(0.2)
        frame = dual.capture_full(idx)
        _save_frame(frame, idx, f"_burst_lp{lp:.2f}")
    st.lp = base_lp
    dual.apply_controls(idx)


def ev_bracket(dual, idx):
    st = dual.states[idx]
    base_ev = st.ev
    for ev_off in [-1.0, -0.5, 0.0, 0.5, 1.0]:
        ev = clamp(base_ev + ev_off, EV_MIN, EV_MAX)
        st.ev = ev
        dual.apply_controls(idx)
        time.sleep(0.2)
        frame = dual.capture_full(idx)
        _save_frame(frame, idx, f"_bracket_ev{ev:+.1f}")
    st.ev = base_ev
    dual.apply_controls(idx)


def _add_osd(frame, state, cam_idx, active):
    h, w = frame.shape[:2]
    text = f"cam{cam_idx} LP={state.lp:.2f} EV={state.ev:+.1f} Z={state.zoom:.1f}x"
    cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0) if active else (200, 200, 200), 2)
    res = "FULL" if state.save_full else "HALF"
    cv2.putText(frame, res, (10, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 200, 255), 1)
    if active:
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 255, 0), 3)


def print_help():
    print("""
  = / -       LP ±0.1
  ] / [       LP ±0.5
  . / ,       LP ±1.0
  t           单次自动对焦
  e / w       EV ±0.5
  z / x       放大 / 缩小
  i/k/j/l     上/下/左/右平移
  r           重置缩放
  s           当前相机拍单张
  S           双摄各拍一张
  b           burst 5 张
  n           EV bracket 5 张
  m           切换保存分辨率 full/half
  f           打印状态
  h           帮助
  Tab         切换活动相机
  q           退出
""")


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    dual = DualCam()
    active = 0
    cv2.namedWindow("DualCam", cv2.WINDOW_NORMAL)
    print("双摄预览启动，按 h 查看帮助")

    try:
        while True:
            frames = []
            for idx in range(2):
                f = dual.grab_frame(idx)
                f = cv2.resize(f, PREVIEW_SIZE)
                _add_osd(f, dual.states[idx], idx, idx == active)
                frames.append(f)
            combined = np.hstack(frames)
            cv2.imshow("DualCam", combined)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('\t'):
                active = 1 - active
                print(f"切换到 cam{active}")
            elif key == ord('='):
                dual.states[active].lp = clamp(dual.states[active].lp + 0.1, LP_MIN, LP_MAX)
                dual.apply_controls(active)
            elif key == ord('-'):
                dual.states[active].lp = clamp(dual.states[active].lp - 0.1, LP_MIN, LP_MAX)
                dual.apply_controls(active)
            elif key == ord(']'):
                dual.states[active].lp = clamp(dual.states[active].lp + 0.5, LP_MIN, LP_MAX)
                dual.apply_controls(active)
            elif key == ord('['):
                dual.states[active].lp = clamp(dual.states[active].lp - 0.5, LP_MIN, LP_MAX)
                dual.apply_controls(active)
            elif key == ord('.'):
                dual.states[active].lp = clamp(dual.states[active].lp + 1.0, LP_MIN, LP_MAX)
                dual.apply_controls(active)
            elif key == ord(','):
                dual.states[active].lp = clamp(dual.states[active].lp - 1.0, LP_MIN, LP_MAX)
                dual.apply_controls(active)
            elif key == ord('e'):
                dual.states[active].ev = clamp(dual.states[active].ev + 0.5, EV_MIN, EV_MAX)
                dual.apply_controls(active)
            elif key == ord('w'):
                dual.states[active].ev = clamp(dual.states[active].ev - 0.5, EV_MIN, EV_MAX)
                dual.apply_controls(active)
            elif key == ord('z'):
                dual.states[active].zoom = min(dual.states[active].zoom * ZOOM_STEP, ZOOM_MAX)
                dual.apply_zoom(active)
            elif key == ord('x'):
                dual.states[active].zoom = max(dual.states[active].zoom / ZOOM_STEP, ZOOM_MIN)
                dual.apply_zoom(active)
            elif key == ord('i'):
                dual.states[active].zoom_cy = clamp(dual.states[active].zoom_cy - PAN_STEP, 0, 1)
                dual.apply_zoom(active)
            elif key == ord('k'):
                dual.states[active].zoom_cy = clamp(dual.states[active].zoom_cy + PAN_STEP, 0, 1)
                dual.apply_zoom(active)
            elif key == ord('j'):
                dual.states[active].zoom_cx = clamp(dual.states[active].zoom_cx - PAN_STEP, 0, 1)
                dual.apply_zoom(active)
            elif key == ord('l'):
                dual.states[active].zoom_cx = clamp(dual.states[active].zoom_cx + PAN_STEP, 0, 1)
                dual.apply_zoom(active)
            elif key == ord('r'):
                dual.states[active].zoom = 1.0
                dual.states[active].zoom_cx = 0.5
                dual.states[active].zoom_cy = 0.5
                dual.apply_zoom(active)
            elif key == ord('t'):
                dual.autofocus_once(active)
            elif key == ord('s'):
                save_single(dual, active)
            elif key == ord('S'):
                save_both(dual)
            elif key == ord('b'):
                save_burst(dual, active)
            elif key == ord('n'):
                ev_bracket(dual, active)
            elif key == ord('m'):
                dual.states[active].save_full = not dual.states[active].save_full
                res = "full" if dual.states[active].save_full else "half"
                print(f"[cam{active}] 保存分辨率切换为 {res}")
            elif key == ord('f'):
                for idx in range(2):
                    print(dual.states[idx].info(idx))
            elif key == ord('h'):
                print_help()
    finally:
        dual.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
