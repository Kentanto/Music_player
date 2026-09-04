import sys
import atexit
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

try:
    import win32con
except ImportError:
    win32con = None

HAS_WINDOWS_HOTKEYS = sys.platform == "win32"

VK_MEDIA_PLAY_PAUSE = getattr(win32con, "VK_MEDIA_PLAY_PAUSE", 0xB3)
VK_MEDIA_NEXT_TRACK = getattr(win32con, "VK_MEDIA_NEXT_TRACK", 0xB0)
VK_MEDIA_PREV_TRACK = getattr(win32con, "VK_MEDIA_PREV_TRACK", 0xB1)
WM_HOTKEY = getattr(win32con, "WM_HOTKEY", 0x0312)

USER32 = ctypes.windll.user32
USER32.RegisterHotKey.argtypes = [wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT]
USER32.RegisterHotKey.restype = wintypes.BOOL
USER32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
USER32.UnregisterHotKey.restype = wintypes.BOOL


class MediaHotkeyFilter(QAbstractNativeEventFilter):
    """Native Windows hotkey filter for media keys."""

    HOTKEY_IDS = {
        1: VK_MEDIA_PLAY_PAUSE,
        2: VK_MEDIA_NEXT_TRACK,
        3: VK_MEDIA_PREV_TRACK,
    }

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.hwnd = int(window.winId())
        self.registered_ids = []
        self._register_hotkeys()
        atexit.register(self.unregister_hotkeys)

    def _register_hotkeys(self):
        if not HAS_WINDOWS_HOTKEYS:
            return

        for hotkey_id, vk in self.HOTKEY_IDS.items():
            if USER32.RegisterHotKey(self.hwnd, hotkey_id, 0, vk):
                self.registered_ids.append(hotkey_id)
            else:
                print(f"Warning: failed to register global hotkey {vk} (id {hotkey_id})")

    def unregister_hotkeys(self):
        if not HAS_WINDOWS_HOTKEYS:
            return

        for hotkey_id in self.registered_ids:
            try:
                USER32.UnregisterHotKey(self.hwnd, hotkey_id)
            except Exception:
                pass
        self.registered_ids.clear()

    def _message_to_ctypes_ptr(self, message):
        if isinstance(message, tuple) and message:
            message = message[0]

        try:
            return ctypes.c_void_p(int(message))
        except Exception:
            return None

    def nativeEventFilter(self, eventType, message):
        if sys.platform != "win32":
            return False, 0

        if eventType not in ("windows_generic_MSG", b"windows_generic_MSG"):
            return False, 0

        msg_ptr = self._message_to_ctypes_ptr(message)
        if not msg_ptr:
            return False, 0

        try:
            msg = ctypes.cast(msg_ptr, ctypes.POINTER(wintypes.MSG)).contents
        except Exception:
            return False, 0

        if msg.message != WM_HOTKEY:
            return False, 0

        hotkey_id = msg.wParam
        if hotkey_id == 1:
            self.window.play_pause_track.emit()
        elif hotkey_id == 2:
            self.window.next_track.emit()
        elif hotkey_id == 3:
            self.window.prev_track.emit()

        return True, 0


def install_media_hotkeys(app, window):
    """Install global media hotkeys on Windows if available."""
    if not HAS_WINDOWS_HOTKEYS:
        return None

    media_filter = MediaHotkeyFilter(window)
    app.installNativeEventFilter(media_filter)
    return media_filter
