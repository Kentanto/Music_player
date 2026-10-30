fixes/features needed:
- cec remote needs fast pause availability
- cec remote needs a fast fullscreen button
- cec needs a ch override to be right click for mouse instead of ch



surface logs cmd;
kent@kent-Surface-Pro:~$ v4l2-ctl -d /dev/video14 --list-formats-ext done;  v
ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture

	[0]: 'YUYV' (YUYV 4:2:2)
		Size: Discrete 1920x1080
			Interval: Discrete 0.033s (30.000 fps)
			Interval: Discrete 0.040s (25.000 fps)
			Interval: Discrete 0.050s (20.000 fps)
			Interval: Discrete 0.067s (15.000 fps)
			Interval: Discrete 0.100s (10.000 fps)
			Interval: Discrete 0.200s (5.000 fps)
v: command not found
kent@kent-Surface-Pro:~$ v4l2-ctl -d /dev/video15 --list-formats-ext done;
ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture

kent@kent-Surface-Pro:~$ v4l2-ctl -d /dev/video16 --list-formats-ext done;
ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture

	[0]: 'YUYV' (YUYV 4:2:2)
		Size: Discrete 320x180
			Interval: Discrete 0.033s (30.000 fps)
			Interval: Discrete 0.040s (25.000 fps)
			Interval: Discrete 0.050s (20.000 fps)
			Interval: Discrete 0.067s (15.000 fps)
			Interval: Discrete 0.100s (10.000 fps)
			Interval: Discrete 0.200s (5.000 fps)
		Size: Discrete 1280x720
			Interval: Discrete 0.033s (30.000 fps)
			Interval: Discrete 0.040s (25.000 fps)
			Interval: Discrete 0.050s (20.000 fps)
			Interval: Discrete 0.067s (15.000 fps)
			Interval: Discrete 0.100s (10.000 fps)
			Interval: Discrete 0.200s (5.000 fps)
		Size: Discrete 640x480
			Interval: Discrete 0.033s (30.000 fps)
			Interval: Discrete 0.040s (25.000 fps)
			Interval: Discrete 0.050s (20.000 fps)
			Interval: Discrete 0.067s (15.000 fps)
			Interval: Discrete 0.100s (10.000 fps)
			Interval: Discrete 0.200s (5.000 fps)
kent@kent-Surface-Pro:~$ v4l2-ctl -d /dev/video17 --list-formats-ext done;
ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture
