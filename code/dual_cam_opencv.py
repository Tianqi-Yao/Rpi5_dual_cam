from picamera2 import Picamera2
import cv2
import numpy as np
import time
import os
import sys

# ── 配置 ──────────────────────────────────────────────────────────────────────
# 切换注释选择传感器模式
# MODE = "16mp"
MODE = "64mp"

RESOLUTION_16MP = (4656, 3496)
RESOLUTION_64MP = (9248, 6944)

PREVIEW_SIZE  = (1024, 600)    # 预览分辨率（两路各自）
DISPLAY_SCALE = 0.5            # 拼接后缩放
FLIP          = False
SAVE_DIR      = "/home/paalab/Desktop/output"
# ──────────────────────────────────────────────────────────────────────────────

SAVE_RESOLUTION = RESOLUTION_16MP if MODE == "16mp" else RESOLUTION_64MP
print(f"[INFO] Mode: {MODE}  |  Save resolution: {SAVE_RESOLUTION[0]}x{SAVE_RESOLUTION[1]}")


def open_cameras():
    cams = []
    for idx in range(2):
        try:
            cam = Picamera2(idx)
            cfg = cam.create_preview_configuration(
                main={"size": PREVIEW_SIZE, "format": "BGR888"},
                controls={"FrameRate": 30},
                buffer_count=2,
            )
            cam.configure(cfg)
            cam.start()
            cams.append(cam)
            print(f"[INFO] Camera {idx} started")
        except Exception as e:
            print(f"[ERROR] Camera {idx} failed: {e}")
            for c in cams:
                c.stop()
            sys.exit(1)
    return cams


def capture_hires(cam, resolution):
    """停预览 → 高分辨率拍照 → 恢复预览"""
    cam.stop()
    cfg = cam.create_still_configuration(
        main={"size": resolution, "format": "BGR888"}
    )
    cam.configure(cfg)
    cam.start()
    frame = cam.capture_array()
    cam.stop()

    # 恢复预览配置
    cfg_prev = cam.create_preview_configuration(
        main={"size": PREVIEW_SIZE, "format": "BGR888"},
        controls={"FrameRate": 30},
        buffer_count=2,
    )
    cam.configure(cfg_prev)
    cam.start()
    return frame


def release_cameras(cams):
    for cam in cams:
        try:
            cam.stop()
        except Exception:
            pass
    cv2.destroyAllWindows()
    print("[INFO] Cameras released")


def add_overlay(frame, label, fps):
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (200, 50), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)
    cv2.putText(frame, label,            (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.putText(frame, f"{fps:.1f} fps", (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 120),  2)
    return frame


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    cams = open_cameras()
    cam0, cam1 = cams

    win_name = f"Dual Camera [{MODE.upper()}]  |  S=save snapshot  |  Q=quit"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)

    frame_count = 0
    t_start = time.monotonic()
    fps = 0.0

    try:
        while True:
            frame0 = cam0.capture_array()
            frame1 = cam1.capture_array()

            if FLIP:
                frame0 = cv2.flip(frame0, 1)
                frame1 = cv2.flip(frame1, 1)

            frame0 = add_overlay(frame0, "CAM 0", fps)
            frame1 = add_overlay(frame1, "CAM 1", fps)

            combined = np.hstack((frame0, frame1))

            if DISPLAY_SCALE != 1.0:
                disp_w = int(combined.shape[1] * DISPLAY_SCALE)
                disp_h = int(combined.shape[0] * DISPLAY_SCALE)
                combined = cv2.resize(combined, (disp_w, disp_h), interpolation=cv2.INTER_LINEAR)

            cv2.imshow(win_name, combined)

            frame_count += 1
            if frame_count % 30 == 0:
                fps = 30 / (time.monotonic() - t_start)
                t_start = time.monotonic()

            key = cv2.waitKey(1) & 0xFF

            if key == ord('q'):
                print("[INFO] Quit")
                break

            elif key == ord('s'):
                ts = time.strftime("%Y%m%d_%H%M%S")
                print(f"[INFO] Capturing {MODE.upper()} ({SAVE_RESOLUTION[0]}x{SAVE_RESOLUTION[1]}) ...")

                img0 = capture_hires(cam0, SAVE_RESOLUTION)
                img1 = capture_hires(cam1, SAVE_RESOLUTION)

                path0 = f"{SAVE_DIR}/{ts}_{MODE}_cam0.jpg"
                path1 = f"{SAVE_DIR}/{ts}_{MODE}_cam1.jpg"
                cv2.imwrite(path0, img0, [cv2.IMWRITE_JPEG_QUALITY, 95])
                cv2.imwrite(path1, img1, [cv2.IMWRITE_JPEG_QUALITY, 95])
                print(f"[INFO] Saved:\n  {path0}\n  {path1}")

    except KeyboardInterrupt:
        print("[INFO] Interrupted")
    finally:
        release_cameras(cams)


if __name__ == "__main__":
    main()