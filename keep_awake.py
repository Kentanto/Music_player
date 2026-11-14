"""Keeps the screen on (and the PC awake) while fullscreen playback runs.

The old approach faked a keypress every 25 s with `xdotool`. That is
unreliable on modern Linux desktops: Fedora runs GNOME/KDE on Wayland,
where synthetic X11 key events never reach the compositor, so they do
not count as user activity and the screen still blanks. It also silently
did nothing wherever xdotool was not installed.

The reliable way is to ask the desktop itself, and to hold the request
for exactly as long as the fullscreen view is visible:

- Linux:   D-Bus ``org.freedesktop.ScreenSaver.Inhibit`` (implemented by
           GNOME Shell, KDE's ksmserver, XFCE, ...) plus a
           ``systemd-inhibit --what=idle:sleep`` helper process that
           keeps logind from suspending the machine. GNOME gets an
           ``org.gnome.SessionManager.Inhibit`` fallback when the
           freedesktop screensaver service is not on the bus.
- Windows: ``SetThreadExecutionState(ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED)``.
- macOS:   ``caffeinate -di`` helper process.

Every path is wrapped defensively: a failed keep-awake request must
never break playback.
"""

import atexit
import signal
import subprocess
import sys

APP_NAME = "Music Engine"
INHIBIT_REASON = "Fullscreen playback"

# org.gnome.SessionManager Inhibit flags: 4 = suspend, 8 = idle.
GNOME_INHIBIT_SUSPEND = 4
GNOME_INHIBIT_IDLE = 8


def _die_with_parent():
    """preexec_fn hook: ask the kernel to kill us if the app dies.

    Without this an abandoned ``systemd-inhibit sleep infinity`` child
    would keep the PC awake forever after a hard app kill (-9).
    """
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(
            PR_SET_PDEATHSIG, signal.SIGKILL
        )
    except Exception:
        pass


class ScreenWakeLock:
    """Acquire when the fullscreen view shows, release when it hides."""

    def __init__(self):
        self._active = False
        self._screensaver_cookie = None   # org.freedesktop.ScreenSaver
        self._gnome_cookie = None         # org.gnome.SessionManager
        self._helper_process = None       # systemd-inhibit / caffeinate
        self._windows_active = False

    # ── public API ──────────────────────────────────────────────────
    def acquire(self):
        """Hold the screen and the machine awake. Safe to call repeatedly."""
        if self._active:
            return
        self._active = True
        try:
            atexit.register(self.release)
        except Exception:
            pass
        try:
            if sys.platform == "win32":
                self._windows_acquire()
            elif sys.platform == "darwin":
                self._start_helper(["caffeinate", "-di"])
            else:
                self._linux_acquire()
        except Exception:
            pass

    def release(self):
        """Let the desktop blank/suspend again. Safe to call repeatedly."""
        if not self._active:
            return
        self._active = False
        try:
            atexit.unregister(self.release)
        except Exception:
            pass
        try:
            if sys.platform == "win32":
                self._windows_release()
            if self._gnome_cookie is not None:
                cookie = self._gnome_cookie
                self._gnome_cookie = None
                self._dbus_call(
                    "org.gnome.SessionManager",
                    "/org/gnome/SessionManager",
                    "org.gnome.SessionManager",
                    "UnInhibit",
                    cookie,
                )
            if self._screensaver_cookie is not None:
                cookie = self._screensaver_cookie
                self._screensaver_cookie = None
                self._dbus_call(
                    "org.freedesktop.ScreenSaver",
                    "/org/freedesktop/ScreenSaver",
                    "org.freedesktop.ScreenSaver",
                    "UnInhibit",
                    cookie,
                )
        except Exception:
            pass
        process = self._helper_process
        self._helper_process = None
        if process is not None:
            self._stop_helper(process)
        if process is not None:
            self._stop_helper(process)

    # ── Linux ──────────────────────────────────────────────────────
    def _linux_acquire(self):
        # 1. Ask the desktop's screensaver service to stay off.
        cookie = self._dbus_call(
            "org.freedesktop.ScreenSaver",
            "/org/freedesktop/ScreenSaver",
            "org.freedesktop.ScreenSaver",
            "Inhibit",
            APP_NAME,
            INHIBIT_REASON,
        )
        if cookie is not None:
            self._screensaver_cookie = cookie
        else:
            # 2. GNOME fallback: idle + suspend session inhibitors.
            gnome_cookie = self._dbus_call(
                "org.gnome.SessionManager",
                "/org/gnome/SessionManager",
                "org.gnome.SessionManager",
                "Inhibit",
                APP_NAME,
                0,
                INHIBIT_REASON,
                GNOME_INHIBIT_SUSPEND | GNOME_INHIBIT_IDLE,
            )
            if gnome_cookie is not None:
                self._gnome_cookie = gnome_cookie
        # 3. Keep logind from auto-suspending while fullscreen is up.
        #    Holding this child process open holds the inhibitor.
        self._start_helper(
            [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--mode=block",
                f"--who={APP_NAME}",
                f"--why={INHIBIT_REASON}",
                "sleep",
                "infinity",
            ]
        )

    @staticmethod
    def _dbus_call(service, path, interface, method, *args):
        """One-shot synchronous D-Bus method call. Returns the first
        reply argument (as int) or None if anything goes wrong."""
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
        except ImportError:
            # QtDBus is only shipped on Linux builds of PySide6.
            return None
        try:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return None
            interface_proxy = QDBusInterface(service, path, interface, bus)
            reply = interface_proxy.call(method, *args)
            if reply.type() != QDBusMessage.MessageType.ReplyMessage:
                return None
            values = reply.arguments()
            if not values or values[0] is None:
                return None
            return int(values[0])
        except Exception:
            return None

    # ── helper process (Linux systemd-inhibit / macOS caffeinate) ───
    def _start_helper(self, command):
        try:
            kwargs = {}
            if sys.platform.startswith("linux"):
                # Never outlive the app, even on a hard kill -9.
                kwargs["preexec_fn"] = _die_with_parent
            self._helper_process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except Exception:
            self._helper_process = None

    @staticmethod
    def _stop_helper(process):
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    # ── Windows ────────────────────────────────────────────────────
    def _windows_acquire(self):
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_DISPLAY_REQUIRED = 0x00000002
        ES_SYSTEM_REQUIRED = 0x00000001
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
        )
        self._windows_active = result != 0

    def _windows_release(self):
        if not self._windows_active:
            return
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        self._windows_active = False

