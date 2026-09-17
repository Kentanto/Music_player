"""MOCUTE-052 / S23-AUTO raw input handler.

Cross-platform raw controller capture.

* Linux   : evdev thread with grab()  (isolates controller from X11/Wayland)
* Windows : QAbstractNativeEventFilter intercepting WM_APPCOMMAND / WM_KEYDOWN
            before Qt or the desktop shell processes them.
"""
from __future__ import annotations

import select
import sys
import time

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

# -- Playback / UI signals ------------------------------------

class _SignalsMixin:
    play_pause           = Signal()
    next_track           = Signal()
    previous_track       = Signal()
    stop_requested       = Signal()
    back_requested       = Signal()
    navigation           = Signal(str)      # up | down | left | right
    select_requested     = Signal()
    volume_up            = Signal()
    volume_down          = Signal()
    fullscreen_requested = Signal()


# ============================================================
# Linux backend (evdev)
# ============================================================

HAS_EVDEV = False
try:
    import evdev
    from evdev import InputDevice, list_devices, ecodes
    HAS_EVDEV = True
except ImportError:
    pass

DEVICE_NAME_PATTERNS = [
    "mocute", "MOCUTE", "GamePad Plus", "Gamepad Plus",
    "S23 AUTO", "s23 auto", "Bluetooth Keyboard", "Bluetooth Mouse",
    "Consumer Control", "Media Center",
]

if HAS_EVDEV:
    _EV_KEY_MAP = {
        ecodes.KEY_UP:          "navigation:up",
        ecodes.KEY_DOWN:        "navigation:down",
        ecodes.KEY_LEFT:        "navigation:left",
        ecodes.KEY_RIGHT:       "navigation:right",
        ecodes.KEY_VOLUMEUP:    "volume_up",
        ecodes.KEY_VOLUMEDOWN:  "volume_down",
        ecodes.KEY_NEXTSONG:    "next_track",
        ecodes.KEY_PREVIOUSSONG: "previous_track",
        ecodes.KEY_ENTER:       "select_requested",
        ecodes.KEY_OK:          "select_requested",
        ecodes.KEY_BACK:        "back_requested",
        ecodes.KEY_ESC:         "back_requested",
        ecodes.KEY_HOMEPAGE:    "back_requested",
        ecodes.KEY_PLAYPAUSE:   "play_pause",
        ecodes.KEY_PLAY:        "play_pause",
        ecodes.KEY_PAUSE:       "play_pause",
        ecodes.KEY_STOPCD:      "stop_requested",
        # Swallow modifiers silently
        ecodes.KEY_LEFTCTRL:    None,
        ecodes.KEY_RIGHTCTRL:   None,
        ecodes.KEY_LEFTSHIFT:   None,
        ecodes.KEY_RIGHTSHIFT:  None,
        ecodes.KEY_LEFTALT:     None,
        ecodes.KEY_RIGHTALT:    None,
        ecodes.KEY_LEFTMETA:    None,
        ecodes.KEY_RIGHTMETA:   None,
    }
else:
    _EV_KEY_MAP = {}


class _LinuxBackend(QThread):
    """Poll evdev devices in a background thread."""

    def __init__(self, listener, device_path=None, scan_interval=3.0):
        super().__init__()
        self._listener = listener
        self._device_path = device_path
        self._scan_interval = scan_interval
        self._running = False
        self._last_key_code = None
        self._last_time = 0.0
        self._debounce = 0.08

    def stop(self):
        self._running = False
        self.wait(3000)

    def _find_devices(self):
        found = []
        if self._device_path:
            try:
                found.append(InputDevice(self._device_path))
            except OSError:
                pass
            return found
        for path in list_devices():
            try:
                dev = InputDevice(path)
                if any(p in (dev.name or "") for p in DEVICE_NAME_PATTERNS):
                    found.append(dev)
            except OSError:
                continue
        return found

    def run(self):
        self._running = True
        open_devices = []

        while self._running:
            if not open_devices:
                for dev in self._find_devices():
                    try:
                        dev.grab()
                        open_devices.append(dev)
                        print(f"[MOCUTE] grabbed {dev.path} {dev.name!r}", flush=True)
                    except OSError as exc:
                        print(f"[MOCUTE] grab failed {dev.path}: {exc}", flush=True)
                        try:
                            dev.close()
                        except OSError:
                            pass
                if not open_devices:
                    time.sleep(self._scan_interval)
                    continue

            readable = []
            for dev in list(open_devices):
                try:
                    readable.append(dev.fd)
                except (OSError, ValueError):
                    open_devices.remove(dev)

            if not readable:
                time.sleep(self._scan_interval)
                continue

            try:
                rds, _, _ = select.select(readable, [], [], self._scan_interval)
            except (select.error, OSError):
                continue

            for fd in rds:
                for dev in open_devices:
                    if dev.fd == fd:
                        try:
                            for event in dev.read():
                                self._handle_event(event)
                        except (OSError, BlockingIOError):
                            pass
                        break

        for dev in open_devices:
            for meth in ("ungrab", "close"):
                try:
                    getattr(dev, meth)()
                except OSError:
                    pass
        print("[MOCUTE] stopped", flush=True)

    def _handle_event(self, event):
        if not HAS_EVDEV or event.type != ecodes.EV_KEY:
            return
        if event.value == 0:
            if event.code == self._last_key_code:
                self._last_key_code = None
            return
        if event.value == 2:
            return
        now = time.monotonic()
        if event.code == self._last_key_code and (now - self._last_time) < self._debounce:
            return
        self._last_key_code = event.code
        self._last_time = now

        action = _EV_KEY_MAP.get(event.code)
        if action is None:
            print(f"[MOCUTE] swallowed {ecodes.KEY.get(event.code, event.code)}", flush=True)
            return
        print(f"[MOCUTE] {ecodes.KEY.get(event.code, event.code)} -> {action}", flush=True)
        self._listener._dispatch(action)


# ============================================================
# Windows backend (native event filter)
# ============================================================

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    from PySide6.QtCore import QAbstractNativeEventFilter

    _USER32 = ctypes.windll.user32

    class _WindowsMocuteFilter(QAbstractNativeEventFilter):
        """Intercept WM_APPCOMMAND and WM_KEYDOWN before Qt processes them."""

        WM_KEYDOWN    = 0x0100
        WM_SYSKEYDOWN = 0x0104
        WM_APPCOMMAND = 0x0319

        # VK codes
        VK_RETURN      = 0x0D
        VK_BACK        = 0x08
        VK_ESCAPE      = 0x1B
        VK_SPACE       = 0x20
        VK_LEFT        = 0x25
        VK_UP          = 0x26
        VK_RIGHT       = 0x27
        VK_DOWN        = 0x28
        VK_VOLUME_UP   = 0xAF
        VK_VOLUME_DOWN = 0xAE
        VK_VOLUME_MUTE = 0xAD
        VK_MEDIA_NEXT  = 0xB0
        VK_MEDIA_PREV  = 0xB1
        VK_MEDIA_STOP  = 0xB2
        VK_MEDIA_PLAY  = 0xB3

        # APPCOMMAND constants
        AC_VOLUME_UP   = 10
        AC_VOLUME_DOWN = 9
        AC_MEDIA_NEXT  = 11
        AC_MEDIA_PREV  = 12
        AC_MEDIA_STOP  = 13
        AC_MEDIA_PLAY  = 14
        AC_MEDIA_PLAY2 = 46
        AC_MEDIA_PAUSE = 47

        def __init__(self, listener):
            super().__init__()
            self._listener = listener
            self._last_cmd = None
            self._last_cmd_time = 0.0
            self._cmd_debounce = 0.12
            self._last_vk = None
            self._last_vk_time = 0.0
            self._vk_debounce = 0.08

        def nativeEventFilter(self, eventType, message):
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == self.WM_APPCOMMAND:
                return self._on_appcommand(msg)
            if msg.message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                return self._on_keydown(msg)
            return False, 0

        def _on_appcommand(self, msg):
            cmd = (msg.lParam >> 16) & 0x0FFF
            now = time.monotonic()
            if cmd == self._last_cmd and (now - self._last_cmd_time) < self._cmd_debounce:
                return True, 1
            self._last_cmd = cmd
            self._last_cmd_time = now
            print(f"[MOCUTE] APPCOMMAND cmd={cmd}", flush=True)

            # MOCUTE vendor-specific codes
            if cmd == 49:
                self._listener.navigation.emit("up")
                return True, 1
            if cmd == 50:
                self._listener.navigation.emit("down")
                return True, 1

            _map = {
                self.AC_MEDIA_NEXT:  "next_track",
                self.AC_MEDIA_PREV:  "previous_track",
                self.AC_MEDIA_PLAY:  "play_pause",
                self.AC_MEDIA_STOP:  "stop_requested",
            }
            action = _map.get(cmd)
            if action:
                self._listener._dispatch(action)
                return True, 1
            return True, 1   # swallow unknown vendor commands

        def _on_keydown(self, msg):
            vk = msg.wParam
            now = time.monotonic()
            if vk == self._last_vk and (now - self._last_vk_time) < self._vk_debounce:
                return True, 1
            self._last_vk = vk
            self._last_vk_time = now
            print(f"[MOCUTE] KEYDOWN VK={vk} (0x{vk:02X})", flush=True)

            _vk_map = {
                self.VK_RETURN:     "select_requested",
                self.VK_SPACE:      "play_pause",
                self.VK_BACK:       "back_requested",
                self.VK_ESCAPE:     "back_requested",
                self.VK_UP:         "navigation:up",
                self.VK_DOWN:       "navigation:down",
                self.VK_LEFT:       "navigation:left",
                self.VK_RIGHT:      "navigation:right",
                self.VK_MEDIA_NEXT: "next_track",
                self.VK_MEDIA_PREV: "previous_track",
                self.VK_MEDIA_STOP: "stop_requested",
                self.VK_MEDIA_PLAY: "play_pause",
            }
            action = _vk_map.get(vk)
            if action:
                self._listener._dispatch(action)
                return True, 1
            return False, 0   # let normal keyboard keys through


    class _WindowsBackend:
        """Manages the native event filter lifetime."""

        def __init__(self, listener):
            self._listener = listener
            self._filter = None

        def start(self):
            self._filter = _WindowsMocuteFilter(self._listener)
            app = QApplication.instance()
            if app:
                app.installNativeEventFilter(self._filter)
            print("[MOCUTE] Windows native filter installed", flush=True)

        def stop(self):
            if self._filter:
                app = QApplication.instance()
                if app:
                    app.removeNativeEventFilter(self._filter)
                self._filter = None
                print("[MOCUTE] Windows native filter removed", flush=True)

else:
    class _WindowsBackend:
        def start(self):
            print("[MOCUTE] Windows backend unavailable on this platform", flush=True)
        def stop(self):
            pass


# ============================================================
# Public interface
# ============================================================

class MocuteListener(_SignalsMixin, QObject):
    """Cross-platform raw MOCUTE controller handler.

    Usage:
        listener = MocuteListener(parent)
        listener.navigation.connect(window.navigate)
        listener.play_pause.connect(player.toggle)
        if listener.available():
            listener.start()
        # ... later
        listener.stop()
    """

    _SIMPLE_ACTIONS = frozenset({
        "play_pause", "next_track", "previous_track", "stop_requested",
        "back_requested", "select_requested",
        "volume_up", "volume_down", "fullscreen_requested",
    })

    def __init__(self, parent=None, *, device_path=None, scan_interval=3.0):
        super().__init__(parent)
        self._device_path = device_path
        self._scan_interval = scan_interval
        self._backend = None
        # Shared debounce state
        self._last_action = None
        self._last_action_time = 0.0
        self._repeat_delay = 0.35
        self._repeat_rate = 0.12
        self._debounce_time = 0.08

    # ---- Public API ----

    def available(self) -> bool:
        if HAS_EVDEV:
            return bool(self._find_devices())
        if sys.platform == "win32":
            return True   # native filter always available to try
        return False

    def start(self):
        if self._backend is not None:
            return
        if HAS_EVDEV:
            self._backend = _LinuxBackend(self, self._device_path, self._scan_interval)
            self._backend.start()
        elif sys.platform == "win32":
            self._backend = _WindowsBackend(self)
            self._backend.start()
        else:
            print("[MOCUTE] no backend for this platform", flush=True)

    def stop(self):
        if self._backend is not None:
            self._backend.stop()
            self._backend = None

    def _find_devices(self):
        found = []
        if self._device_path:
            try:
                found.append(InputDevice(self._device_path))
            except OSError:
                pass
            return found
        for path in list_devices():
            try:
                dev = InputDevice(path)
                if any(p in (dev.name or "") for p in DEVICE_NAME_PATTERNS):
                    found.append(dev)
            except OSError:
                continue
        return found

    # ---- Shared action dispatcher ----

    def _dispatch(self, action):
        """Route a parsed action string to the correct Qt signal,
        with debounce / repeat-rate gating.
        """
        now = time.monotonic()
        is_same = action == self._last_action
        elapsed = now - self._last_action_time

        if is_same:
            threshold = self._repeat_delay if elapsed < self._repeat_delay * 2 else self._repeat_rate
            if elapsed < threshold:
                return
        else:
            if elapsed < self._debounce_time:
                return

        self._last_action = action
        self._last_action_time = now
        print(f"[MOCUTE] emit {action}", flush=True)

        if action.startswith("navigation:"):
            self.navigation.emit(action.split(":", 1)[1])
        elif action in self._SIMPLE_ACTIONS:
            getattr(self, action).emit()
        else:
            print(f"[MOCUTE] unknown action: {action}")
