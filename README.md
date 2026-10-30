fixes/features needed:
- cec remote needs fast pause availability
- cec remote needs a fast fullscreen button
- cec needs a ch override to be right click for mouse instead of ch



surface logs cmd;
for n in 14 15 16 17; do
    echo "========== /dev/video$n =========="
    v4l2-ctl -d /dev/video$n --list-formats-ext
done
