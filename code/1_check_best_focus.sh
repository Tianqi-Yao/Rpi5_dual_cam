#!/bin/bash
# Open the dual-camera preview with keyboard focus control.
# Tab=switch active camera, +/- type keys to adjust LensPosition, s=save, q=quit.
cd "$(dirname "$0")"
python3 dual_cam_preview_focus.py
