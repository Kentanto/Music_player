fixes/features needed:
- cec remote needs fast pause availability
- cec remote needs a fast fullscreen button
- cec needs a ch override to be right click for mouse instead of ch



kent@kent-Surface-Pro:~$ sudo evtest /dev/input/event8
[sudo] password for kent:     
Input driver version is 1.0.1
Input device ID: bus 0x0 vendor 0x45e product 0x1f version 0x0
Input device name: "IPTS 045E:001F Touchscreen"
Supported events:
  Event type 0 (EV_SYN)
  Event type 1 (EV_KEY)
    Event code 330 (BTN_TOUCH)
  Event type 3 (EV_ABS)
    Event code 0 (ABS_X)
      Value      0
      Min        0
      Max    32767
      Resolution     112
    Event code 1 (ABS_Y)
      Value      0
      Min        0
      Max    32767
      Resolution     198
  Event type 4 (EV_MSC)
    Event code 4 (MSC_SCAN)
Properties:
Testing ... (interrupt to exit)
^C
kent@kent-Surface-Pro:~$ 


