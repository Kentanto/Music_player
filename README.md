fixes/features needed:
- cec remote needs fast pause availability
- cec remote needs a fast fullscreen button
- cec needs a ch override to be right click for mouse instead of ch



surface logs cmd;

kent@kent-Surface-Pro:~$ lsusb
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 002: ID 046d:0861 Logitech, Inc. USB2.0 Hub
Bus 001 Device 003: ID 046d:0864 Logitech, Inc. Logitech SmartDock
Bus 001 Device 004: ID 046d:0861 Logitech, Inc. USB2.0 Hub
Bus 001 Device 005: ID 046d:0865 Logitech, Inc. Logitech SmartDock
Bus 001 Device 006: ID 413c:301a Dell Computer Corp. Dell MS116 Optical Mouse
Bus 001 Device 007: ID 145f:0322 Trust Trust GXT 866 TORIX
Bus 001 Device 008: ID 1286:204c Marvell Semiconductor, Inc. Bluetooth and Wireless LAN Composite
Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub
Bus 002 Device 002: ID 046d:0862 Logitech, Inc. USB3.0 Hub
Bus 002 Device 003: ID 0bda:8153 Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter
Bus 002 Device 004: ID 046d:0862 Logitech, Inc. USB3.0 Hub
Bus 002 Device 005: ID 046d:0863 Logitech, Inc. Logitech SmartDock HDMI Capture
kent@kent-Surface-Pro:~$ ls -l /dev/video*
crw-rw----+ 1 root video 81,  0 Oct 30 15:11 /dev/video0
crw-rw----+ 1 root video 81,  1 Oct 30 15:11 /dev/video1
crw-rw----+ 1 root video 81, 12 Oct 30 15:11 /dev/video10
crw-rw----+ 1 root video 81, 13 Oct 30 15:11 /dev/video11
crw-rw----+ 1 root video 81, 14 Oct 30 15:11 /dev/video12
crw-rw----+ 1 root video 81, 15 Oct 30 15:11 /dev/video13
crw-rw----+ 1 root video 81, 24 Oct 30 15:11 /dev/video14
crw-rw----+ 1 root video 81, 25 Oct 30 15:11 /dev/video15
crw-rw----+ 1 root video 81, 26 Oct 30 15:11 /dev/video16
crw-rw----+ 1 root video 81, 27 Oct 30 15:11 /dev/video17
crw-rw----+ 1 root video 81,  2 Oct 30 15:11 /dev/video2
crw-rw----+ 1 root video 81,  3 Oct 30 15:11 /dev/video3
crw-rw----+ 1 root video 81,  4 Oct 30 15:11 /dev/video4
crw-rw----+ 1 root video 81,  5 Oct 30 15:11 /dev/video5
crw-rw----+ 1 root video 81,  6 Oct 30 15:11 /dev/video6
crw-rw----+ 1 root video 81,  7 Oct 30 15:11 /dev/video7
crw-rw----+ 1 root video 81,  8 Oct 30 15:11 /dev/video8
crw-rw----+ 1 root video 81,  9 Oct 30 15:11 /dev/video9
kent@kent-Surface-Pro:~$ sudo apt install v4l-utils
[sudo] password for kent:     
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
The following additional packages will be installed:
  libv4l2rds0t64
The following NEW packages will be installed:
  libv4l2rds0t64 v4l-utils
0 upgraded, 2 newly installed, 0 to remove and 0 not upgraded.
Need to get 742 kB of archives.
After this operation, 2 442 kB of additional disk space will be used.
Do you want to continue? [Y/n] y
Get:1 http://archive.ubuntu.com/ubuntu noble/main amd64 libv4l2rds0t64 amd64 1.26.1-4build3 [20,0 kB]
Get:2 http://archive.ubuntu.com/ubuntu noble/universe amd64 v4l-utils amd64 1.26.1-4build3 [722 kB]
Fetched 742 kB in 0s (1 618 kB/s)    
Selecting previously unselected package libv4l2rds0t64:amd64.
(Reading database… 518467 files and directories currently installed.)
Preparing to unpack …/libv4l2rds0t64_1.26.1-4build3_amd64.deb…
Unpacking libv4l2rds0t64:amd64 (1.26.1-4build3)…
Selecting previously unselected package v4l-utils.
Preparing to unpack …/v4l-utils_1.26.1-4build3_amd64.deb…
Unpacking v4l-utils (1.26.1-4build3)…
Setting up libv4l2rds0t64:amd64 (1.26.1-4build3)…
Setting up v4l-utils (1.26.1-4build3)…
Processing triggers for libc-bin (2.39-0ubuntu8.9)…
Processing triggers for man-db (2.12.0-4build2)…
kent@kent-Surface-Pro:~$ v4l2-ctl --list-devices
ipu3-imgu (PCI:0000:00:05.0):
	/dev/video0
	/dev/video1
	/dev/video2
	/dev/video3
	/dev/video4
	/dev/video5
	/dev/video6
	/dev/video7
	/dev/video8
	/dev/video9
	/dev/media0

Intel IPU3 CIO2 (PCI:0000:00:14.3):
	/dev/video10
	/dev/video11
	/dev/video12
	/dev/video13
	/dev/media1

Logitech SmartDock HDMI Capture (usb-0000:00:14.0-3.2):
	/dev/video14
	/dev/video15
	/dev/video16
	/dev/video17
	/dev/media2

kent@kent-Surface-Pro:~$ 
