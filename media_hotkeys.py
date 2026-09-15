"""Windows native media-key interception.

Globally registers VK_MEDIA_PLAY_PAUSE so the play/pause key works
even when the app is not focused.
"""
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
WM_HOTKEY = getattr(win32con, "WM_HOTKEY", 0x0312)

if HAS_WINDOWS_HOTKEYS:
    USER32 = ctypes.windll.user32
    USER32.RegisterHotKey.argtypes = [wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT]
    USER32.RegisterHotKey.restype   = wintypes.BOOL
    USER32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
    USER32.UnregisterHotKey.restype  = wintypes.BOOL


class MediaHotkeyFilter(QAbstractNativeEventFilter):
    """Intercept WM_HOTKEY globally on Windows."""

    HOTKEY_IDS = {1: VK_MEDIA_PLAY_PAUSE}

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
        for hid, vk in self.HOTKEY_IDS.items():
            if USER32.RegisterHotKey(self.hwnd, hid, 0, vk):
                self.registered_ids.append(hid)
            else:
                print(f"[HOTKEY] failed to register {vk} (id {hid})")

    def unregister_hotkeys(self):
        if not HAS_WINDOWS_HOTKEYS:
            return
        for hid in list(self.registered_ids):
            try:
                USER32.UnregisterHotKey(self.hwnd, hid)
            except Exception:
                pass
        self.registered_ids.clear()

    def nativeEventFilter(self, eventType, message):
        if sys.platform != "win32":
            return False, 0
        try:
            msg = ctypes.cast(message.__int__(), ctypes.POINTER(wintypes.MSG)).contents
        except Exception:
            # Fallback for QVariant / MsgIn
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents

        if msg.message == WM_HOTKEY:
            hid = msg.wParam
            if hid == 1:
                self.window.play_pause_track.emit()
                return True, 0
        return False, 0


def install_media_hotkeys(app, window):
    if not HAS_WINDOWS_HOTKEYS:
        return None
    f = MediaHotkeyFilter(window)
    app.installNativeEventFilter(f)
    return f
