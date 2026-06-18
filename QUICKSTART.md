# Quick Start

For full details see `README.md`. This file is just the operational checklist.
Run all commands from the `code/` directory.

## 0. Install dependencies (Rpi5, first time)

```bash
sudo apt update
sudo apt install -y python3-picamera2 rpicam-apps
sudo apt install python3-matplotlib python3-opencv
```

## 1. Confirm both cameras are detected + index mapping

```bash
rpicam-hello --list-cameras
```

Note the order: the first entry is `Picamera2(0)`/`cam0`, the second is `Picamera2(1)`/`cam1`.

## 2. Calibrate the best focus value for each camera

```bash
python3 calibration.py --mode normal
```

Check `~/Desktop/images/calibration_*/summary.txt` for the recommended LP per camera.

## 3. Interactive preview / focus / capture

```bash
python3 preview_focus.py
```

Key shortcuts:
- `Tab` switch active camera (cam0/cam1)
- `=`/`-`, `]`/`[`, `.`/`,` adjust focus (fine/medium/coarse)
- `t` one-shot autofocus
- `s` save one shot from active camera, `S` save from both cameras
- `m` toggle 64MP/16MP save resolution
- `h` full help, `q` quit

## 4. Deploy automated capture

**Auto-start on boot (systemd)**:
```bash
bash 1_install_service.sh       # runs batch_capture.py, registers dualcam64.service
sudo systemctl status dualcam64.service
sudo journalctl -u dualcam64.service -f
```

**Stop**:
```bash
bash 2_stop_service.sh
```

## Output directory overview

```
~/Desktop/images/
├── preview_captures/cam{0,1}/   # preview_focus.py
├── calibration_*/cam{0,1}/      # calibration.py
├── auto_focus/cam{0,1}/         # batch_capture.py AF group
├── fixed_focus/cam{0,1}/        # batch_capture.py fixed-LP group
└── batch_log.txt
```
