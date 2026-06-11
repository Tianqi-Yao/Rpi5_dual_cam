#!/bin/bash
# Start a background tmux session running dual_cam_batch_focus_capture.py.
# Attach with: tmux attach -t dualcam64

SESSION="dualcam64"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

tmux has-session -t $SESSION 2>/dev/null
if [ $? != 0 ]; then
    tmux new-session -d -s $SESSION
    tmux rename-window -t $SESSION:0 'Focus'
    tmux send-keys -t $SESSION:Focus "python3 $SCRIPT_DIR/dual_cam_batch_focus_capture.py" C-m
    echo "Session '$SESSION' started."
else
    echo "Session '$SESSION' already running."
fi

echo "Attach with: tmux attach -t $SESSION"
