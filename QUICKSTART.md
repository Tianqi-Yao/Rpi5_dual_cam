# Quick Start

For full details see `README.md`. This file is just the operational checklist.
Run all commands from the `code/` directory.

## 0. Install dependencies (Rpi5, first time)

```bash
sudo apt update
sudo apt install -y python3-picamera2 rpicam-apps tmux
pip install -r requirements.txt   # opencv-python numpy matplotlib
```

## 1. Confirm both cameras are detected + index mapping

```bash
rpicam-hello --list-cameras
```

Note the order: the first entry is `Picamera2(0)`/`cam0`, the second is `Picamera2(1)`/`cam1`.

## 2. Quick check both cameras can capture (16MP, fast)

```bash
python3 dual_cam_capture.py --camera both --mode half
```

Output goes to `~/Desktop/images/captures/`; you should get `..._cam0.jpg` and `..._cam1.jpg`.

## 3. Verify direct 64MP capture (confirm no OOM on Rpi5)

```bash
python3 dual_cam_capture.py --camera both --mode full
```

Check for errors / OOM-killer (`dmesg | tail`). If problems occur, fall back to `--backend rpicam-still` for comparison.

## 4. Calibrate the best focus value for each camera

```bash
python3 dual_cam_calibration.py --camera both --mode normal
```

Check `~/Desktop/images/calibration_*/summary.txt` for the recommended `--lens-position` per camera.

## 5. Interactive preview / focus / capture

```bash
bash 1_check_best_focus.sh
# equivalent to: python3 dual_cam_preview_focus.py
```

Key shortcuts:
- `Tab` switch active camera (cam0/cam1)
- `=`/`-`, `]`/`[`, `.`/`,` adjust focus (fine/medium/coarse)
- `t` one-shot autofocus
- `s` save one shot from active camera, `S` save from both cameras
- `m` toggle 64MP/16MP save resolution, `v` toggle picamera2/rpicam-still backend
- `h` full help, `q` quit

## 6. Deploy automated capture

**Run temporarily (tmux)**:
```bash
bash 2_start_tmux_session.sh        # runs dual_cam_batch_focus_capture.py
tmux attach -t dualcam64            # view
```

**Auto-start on boot (systemd)**:
```bash
bash 3_install_start_auto_services.sh   # runs dual_cam_run.py, registers dualcam64.service
sudo systemctl status dualcam64.service
sudo journalctl -u dualcam64.service -f
```

**Stop**:
```bash
bash 4_stop_auto_run.sh
```

## Output directory overview

```
~/Desktop/images/
├── captures/              # dual_cam_capture.py
├── preview_captures/      # dual_cam_preview_focus.py
├── calibration_*/         # dual_cam_calibration.py
├── manual_focus/cam{0,1}/ # dual_cam_batch_focus_capture.py
├── autofocus_picamera2/cam{0,1}/  # dual_cam_run.py stage 1
├── manualfocus_full/cam{0,1}/     # dual_cam_run.py stage 2
├── batch_log.txt
└── autofocus_log.txt
```
