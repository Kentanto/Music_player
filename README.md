livingroompi@LivingroomPi:~/Music/Music_player/dist/MusicEngine $ sudo ./MusicEngine
qt.multimedia.ffmpeg: Using Qt multimedia with FFmpeg version 7.1.5 LGPL version 2.1 or later
Failed to connect to pipewire instance "Host is down"
PulseAudioService: pa_context_connect() failed
Loaded 0 playlists
Global media hotkeys are unavailable on this platform or missing dependencies.
qt.qpa.services: Failed to register with host portal QDBusError("org.freedesktop.portal.Error.Failed", "Could not register app ID: App info not found for ''")
[CEC-RAW] Driver Info:
[CEC-RAW] Driver Name                : vc4_hdmi
[CEC-RAW] Adapter Name               : vc4-hdmi-1
[CEC-RAW] Capabilities               : 0x0000031e
[CEC-RAW] Logical Addresses
[CEC-RAW] Transmit
[CEC-RAW] Passthrough
[CEC-RAW] Remote Control Support
[CEC-RAW] Connector Info
[CEC-RAW] Reply Vendor ID
[CEC-RAW] Driver version             : 6.18.39
[CEC-RAW] Available Logical Addresses: 1
[CEC-RAW] DRM Connector Info         : card 1, connector 44
[CEC-RAW] Physical Address           : 2.0.0.0
[CEC-RAW] Logical Address Mask       : 0x0010
[CEC-RAW] CEC Version                : 2.0
[CEC-RAW] Vendor ID                  : 0x000c03 (HDMI)
[CEC-RAW] OSD Name                   : 'Playback'
[CEC-RAW] Logical Addresses          : 1 (Allow RC Passthrough)
[CEC-RAW] 
[CEC-RAW] Logical Address          : 4 (Playback Device 1)
[CEC-RAW] Primary Device Type    : Playback
[CEC-RAW] Logical Address Type   : Playback
[CEC-RAW] All Device Types       : Playback
[CEC-RAW] RC TV Profile          : None
[CEC-RAW] Device Features        :
[CEC-RAW] None
[CEC-RAW] 
[CEC-RAW] 
[CEC-RAW] Transmit from Playback Device 1 to all (4 to 15):
[CEC-RAW] ACTIVE_SOURCE (0x82):
[CEC-RAW] phys-addr: 2.0.0.0
[CEC-RAW] Sequence: 536 Tx Timestamp: 25358.612296s
[CEC-RAW] 
[CEC-RAW] 
[CEC-RAW] (warn: State Change events were lost)
[CEC-RAW] 25358.060707: Event: State Change: PA: 2.0.0.0, LA mask: 0x0010
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: select (0x00)
[CEC] parsed code=0x00 action=select_requested
[CEC-EMIT] select_requested()
[NAV] select: button[📋 Playlists]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
Loaded 0 playlists
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: select (0x00)
[CEC] parsed code=0x00 action=select_requested
[CEC-EMIT] select_requested()
[NAV] select: button[📋 Playlists]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
Loaded 0 playlists
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
^[[C[NAV] right: button[📋 Playlists] -> filter
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
^[[A[NAV] up: filter -> button[🔀 Shuffle]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
[NAV] right: auto-start -> queue[-1]
[NAV] right: queue[-1] -> combo[Date Added]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: combo[Date Added] -> filter
[NAV] up: filter -> button[🔀 Shuffle]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: button[🔀 Shuffle] -> queue[-1]
[NAV] up: queue[-1] -> filter
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: filter -> filter
[NAV] up: filter -> button[🔀 Shuffle]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[🔀 Shuffle] -> button[Fullscreen]
[NAV] left: button[Fullscreen] -> button[Next ⏭]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[Next ⏭] -> button[▶ Play]
[NAV] left: button[▶ Play] -> button[⏮ Prev]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[⏮ Prev] -> button[Fullscreen]
[NAV] left: button[Fullscreen] -> button[Next ⏭]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[Next ⏭] -> button[▶ Play]
[NAV] left: button[▶ Play] -> button[⏮ Prev]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[⏮ Prev] -> button[Fullscreen]
[NAV] left: button[Fullscreen] -> button[Next ⏭]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[Next ⏭] -> button[▶ Play]
[NAV] left: button[▶ Play] -> button[⏮ Prev]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: button[⏮ Prev] -> button[Fullscreen]
[NAV] left: button[Fullscreen] -> button[Next ⏭]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: button[Next ⏭] -> slider[seek]
[NAV] up: slider[seek] -> queue[-1]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: queue[-1] -> filter
[NAV] up: filter -> filter
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: left (0x03)
[CEC] parsed code=0x03 action=navigation:left
[CEC-EMIT] navigation('left')
[NAV] left: filter -> button[📋 Playlists]
[NAV] left: button[📋 Playlists] -> button[⏮ Prev]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: button[⏮ Prev] -> slider[seek]
[NAV] up: slider[seek] -> queue[-1]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: queue[-1] -> filter
[NAV] up: filter -> filter
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: up (0x01)
[CEC] parsed code=0x01 action=navigation:up
[CEC-EMIT] navigation('up')
[NAV] up: filter -> button[🔀 Shuffle]
[NAV] up: button[🔀 Shuffle] -> queue[-1]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: down (0x02)
[CEC] parsed code=0x02 action=navigation:down
[CEC-EMIT] navigation('down')
[NAV] down: queue[-1] -> slider[seek]
[NAV] down: slider[seek] -> button[⏮ Prev]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: down (0x02)
[CEC] parsed code=0x02 action=navigation:down
[CEC-EMIT] navigation('down')
[NAV] down: button[⏮ Prev] -> slider[seek]
[NAV] down: slider[seek] -> slider[seek]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
[NAV] right: slider[seek] -> button[▶ Play]
[NAV] right: button[▶ Play] -> button[Next ⏭]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
[NAV] right: button[Next ⏭] -> button[Fullscreen]
[NAV] right: button[Fullscreen] -> button[🔀 Shuffle]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
[NAV] right: button[🔀 Shuffle] -> queue[-1]
[NAV] right: queue[-1] -> combo[Date Added]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
[NAV] right: combo[Date Added] -> button[➕ Add to Playlist]
[NAV] right: button[➕ Add to Playlist] -> cover_art
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_PRESSED (0x44):
[CEC-RAW] ui-cmd: right (0x04)
[CEC] parsed code=0x04 action=navigation:right
[CEC-EMIT] navigation('right')
[NAV] right: cover_art -> filter
[NAV] right: filter -> combo[Date Added]
[CEC-RAW] Received from TV to Playback Device 1 (0 to 4): USER_CONTROL_RELEASED (0x45)
Loaded 0 playlists
Loaded 0 playlists
Loaded 0 playlists
Loaded 0 playlists
livingroompi@LivingroomPi:~/Music/Music_player/dist/MusicEngine $ sudo ./MusicEngine
qt.multimedia.ffmpeg: Using Qt multimedia with FFmpeg version 7.1.5 LGPL version 2.1 or later
Failed to connect to pipewire instance "Host is down"
PulseAudioService: pa_context_connect() failed
Loaded 0 playlists
Global media hotkeys are unavailable on this platform or missing dependencies.
[CEC-RAW] Driver Info:
[CEC-RAW] Driver Name                : vc4_hdmi
[CEC-RAW] Adapter Name               : vc4-hdmi-1
[CEC-RAW] Capabilities               : 0x0000031e
[CEC-RAW] Logical Addresses
[CEC-RAW] Transmit
[CEC-RAW] Passthrough
[CEC-RAW] Remote Control Support
[CEC-RAW] Connector Info
[CEC-RAW] Reply Vendor ID
[CEC-RAW] Driver version             : 6.18.39
[CEC-RAW] Available Logical Addresses: 1
[CEC-RAW] DRM Connector Info         : card 1, connector 44
[CEC-RAW] Physical Address           : 2.0.0.0
[CEC-RAW] Logical Address Mask       : 0x0010
[CEC-RAW] CEC Version                : 2.0
[CEC-RAW] Vendor ID                  : 0x000c03 (HDMI)
[CEC-RAW] OSD Name                   : 'Playback'
[CEC-RAW] Logical Addresses          : 1 (Allow RC Passthrough)
[CEC-RAW] 
[CEC-RAW] Logical Address          : 4 (Playback Device 1)
[CEC-RAW] Primary Device Type    : Playback
[CEC-RAW] Logical Address Type   : Playback
[CEC-RAW] All Device Types       : Playback
[CEC-RAW] RC TV Profile          : None
[CEC-RAW] Device Features        :
[CEC-RAW] None
[CEC-RAW] 
[CEC-RAW] 
[CEC-RAW] Transmit from Playback Device 1 to all (4 to 15):
[CEC-RAW] ACTIVE_SOURCE (0x82):
[CEC-RAW] phys-addr: 2.0.0.0
Loaded 0 playlists
[CEC-RAW] Sequence: 563 Tx Timestamp: 25417.551146s
[CEC-RAW] 
[CEC-RAW] 
[CEC-RAW] (warn: State Change events were lost)
[CEC-RAW] 25416.999542: Event: State Change: PA: 2.0.0.0, LA mask: 0x0010
livingroompi@LivingroomPi:~/Music/Music_player/dist/MusicEngine $  ./MusicEngineqt.multimedia.ffmpeg: Using Qt multimedia with FFmpeg version 7.1.5 LGPL version 2.1 or later
Opened playlist 18 with 206 songs
Global media hotkeys are unavailable on this platform or missing dependencies.
[CEC-RAW] Driver Info:
[CEC-RAW] Driver Name                : vc4_hdmi
[CEC-RAW] Adapter Name               : vc4-hdmi-1
[CEC-RAW] Capabilities               : 0x0000031e
[CEC-RAW] Logical Addresses
[CEC-RAW] Transmit
[CEC-RAW] Passthrough
[CEC-RAW] Remote Control Support
[CEC-RAW] Connector Info
[CEC-RAW] Reply Vendor ID
[CEC-RAW] Driver version             : 6.18.39
[CEC-RAW] Available Logical Addresses: 1
[CEC-RAW] DRM Connector Info         : card 1, connector 44
[CEC-RAW] Physical Address           : 2.0.0.0
[CEC-RAW] Logical Address Mask       : 0x0010
[CEC-RAW] CEC Version                : 2.0
[CEC-RAW] Vendor ID                  : 0x000c03 (HDMI)
[CEC-RAW] OSD Name                   : 'Playback'
[CEC-RAW] Logical Addresses          : 1 (Allow RC Passthrough)
[CEC-RAW] 
[CEC-RAW] Logical Address          : 4 (Playback Device 1)
[CEC-RAW] Primary Device Type    : Playback
[CEC-RAW] Logical Address Type   : Playback
[CEC-RAW] All Device Types       : Playback
[CEC-RAW] RC TV Profile          : None
[CEC-RAW] Device Features        :
[CEC-RAW] None
[CEC-RAW] 
[CEC-RAW] 
[CEC-RAW] Transmit from Playback Device 1 to all (4 to 15):
[CEC-RAW] ACTIVE_SOURCE (0x82):
[CEC-RAW] phys-addr: 2.0.0.0
[CEC-RAW] Sequence: 617 Tx Timestamp: 25454.258287s
[CEC-RAW] Selecting monitor mode failed, you may have to run this as root.
[CEC] WARNING: cec-ctl needs root permissions for --monitor mode.
[CEC]          Run with: sudo python main.py
[CEC]          Or add user to video group: sudo usermod -aG video $USER
[NAV] right: button[📋 Playlists] -> filter
[NAV] left: filter -> button[📋 Playlists]
[NAV] left: button[📋 Playlists] -> button[⏮ Prev]
[NAV] down: button[⏮ Prev] -> slider[seek]
[NAV] right: slider[seek] -> button[▶ Play]
[NAV] up: button[▶ Play] -> slider[seek]
[NAV] right: slider[seek] -> queue[-1]
[NAV] right: queue[0]=ヒカリへ -> combo[Shuffled]
[NAV] right: combo[Shuffled] -> button[➕ Add to Playlist]
[NAV] left: button[➕ Add to Playlist] -> button[⏮ Prev]
[NAV] left: button[⏮ Prev] -> button[Fullscreen]
livingroompi@LivingroomPi:~/Music/Music_player/dist/MusicEngine $ ^C
livingroompi@LivingroomPi:~/Music/Music_player/dist/MusicEngine $ 
