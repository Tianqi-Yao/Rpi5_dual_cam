import os
import subprocess
import cv2

SENSOR_MODES = {
    "full":  (9248, 6944),
    "half":  (4624, 3472),
    "4k":    (3840, 2160),
    "mid":   (2312, 1736),
    "1080p": (1920, 1080),
}

LP_MIN = 0.0
LP_MAX = 16.0
EV_MIN = -4.0
EV_MAX = 4.0

SAVE_DIR_BASE = os.path.expanduser("~/Desktop/images")


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def timestamp():
    import datetime
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def laplacian_sharpness(bgr_frame):
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def get_disk_usage():
    try:
        result = subprocess.run(["df", "-h", os.path.expanduser("~")],
                                capture_output=True, text=True)
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            return f"{parts[2]}/{parts[1]} ({parts[4]})"
    except Exception:
        pass
    return "N/A"


def get_cpu_temp():
    try:
        result = subprocess.run(["vcgencmd", "measure_temp"],
                                capture_output=True, text=True)
        return result.stdout.strip().replace("temp=", "")
    except Exception:
        pass
    return "N/A"


def get_memory_usage():
    try:
        result = subprocess.run(["free", "-m"], capture_output=True, text=True)
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            total, used = int(parts[1]), int(parts[2])
            return f"{used}/{total} MB ({100*used//total}%)"
    except Exception:
        pass
    return "N/A"


def log_system_status(log_fn=print):
    log_fn(f"  disk:  {get_disk_usage()}")
    log_fn(f"  temp:  {get_cpu_temp()}")
    log_fn(f"  mem:   {get_memory_usage()}")
