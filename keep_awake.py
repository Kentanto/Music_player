"""Keeps the screen on (and the PC awake) while fullscreen playback runs.

The old approach faked a keypress every 25 s with `xdotool`. That is
unreliable on modern Linux desktops: Fedora runs GNOME/KDE on Wayland,
where synthetic X11 key events never reach the compositor, so they do
not count as user activity and the screen still blanks.

The reliable way is to ask the desktop itself, and to hold the request
for exactly as long as the fullscreen view is visible:

- Linux:   D-Bus ``org.freedesktop.ScreenSaver.Inhibit`` (GNOME/XFCE
           expose it at /org/freedesktop/ScreenSaver, KDE at
           /ScreenSaver — both are tried) **plus** GNOME's own
           ``org.gnome.SessionManager.Inhibit`` (idle + suspend flags),
           because GNOME drives blanking and auto-suspend from its
           session idle monitor. A ``systemd-inhibit --what=idle:sleep``
           helper process additionally blocks suspend for desktops that
           honour logind inhibitors (KDE does).
- Windows: ``SetThreadExecutionState(ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED)``.
- macOS:   ``caffeinate -di`` helper process.

Every step is logged with a "[keep-awake]" prefix so problems can be
diagnosed from the console instead of failing silently (that is how the
first version's invisible failures went unnoticed). A failed keep-awake
request must never break playback, so all paths stay defensive.

Self-test on the target desktop:  python keep_awake.py --check
"""

import atexit
import signal
import subprocess
import sys
import time

APP_NAME = "Music Engine"
INHIBIT_REASON = "Fullscreen playback"

# org.gnome.SessionManager Inhibit flags: 4 = suspend, 8 = idle.
GNOME_INHIBIT_SUSPEND = 4
GNOME_INHIBIT_IDLE = 8

# org.freedesktop.ScreenSaver lives at different object paths per desktop:
# GNOME/XFCE at /org/freedesktop/ScreenSaver, KDE at /ScreenSaver.
SCREENSAVER_PATHS = (
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/ScreenSaver"),
)


def _log(message):
    # The packaged app runs with a console (spec: console=True) and is
    # usually launched from a terminal — these lines are the keep-awake
    # black box when something misbehaves on another machine.
    print(f"[keep-awake] {message}", flush=True)


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


_QTDBUS = None
_QTDBUS_TRIED = False


def _qtdbus():
    """Import QtDBus once and cache the result (None when unavailable)."""
    global _QTDBUS, _QTDBUS_TRIED
    if not _QTDBUS_TRIED:
        _QTDBUS_TRIED = True
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusMessage
            _QTDBUS = (QDBusConnection, QDBusMessage)
        except Exception as error:
            # QtDBus is only shipped on Linux builds of PySide6; a frozen
            # build can also miss it when bundling went wrong.
            _log(f"QtDBus unavailable ({error}) — screen inhibit cannot work")
    return _QTDBUS


def _dbus_call(service, path, interface, method, args):
    """Synchronous D-Bus method call. Returns the first reply argument
    (as int) or None. Every failure is logged.

    A plain QDBusMessage is sent instead of using QDBusInterface: that
    class depends on remote introspection and silently refuses to send
    when the service cannot be introspected — an invisible failure mode
    that looks exactly like "the screen still blanks".
    """
    qtdbus = _qtdbus()
    if qtdbus is None:
        return None
    QDBusConnection, QDBusMessage = qtdbus
    try:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            _log(f"{method}: session bus is not connected")
            return None
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setArguments(list(args))
        reply = bus.call(message)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            _log(f"{method} failed: {reply.errorMessage()}")
            return None
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None
        values = reply.arguments()
        if not values or values[0] is None:
            return None
        return int(values[0])
    except Exception as error:
        _log(f"{method} exception: {type(error).__name__}: {error}")
        return None


class ScreenWakeLock:
    """Acquire when the fullscreen view shows, release when it hides."""

    def __init__(self):
        self._active = False
        self._inhibits = []            # [(service, path, cookie), ...]
        self._helper_process = None    # systemd-inhibit / caffeinate
        self._windows_active = False

    def describe(self):
        """Human-readable summary of what is holding the screen awake."""
        parts = [f"{service} via {path} (cookie {cookie})"
                 for service, path, cookie in self._inhibits]
        if self._helper_process is not None:
            parts.append("logind idle:sleep helper")
        if self._windows_active:
            parts.append("Windows execution state")
        return "; ".join(parts) if parts else "nothing"

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
        except Exception as error:
            _log(f"acquire problem ({type(error).__name__}: {error}) — "
                 "playback continues, but the screen may blank")
        _log(f"holding the screen awake: {self.describe()}")

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
            for service, path, cookie in self._inhibits:
                self._dbus_call(
                    service, path, service, "UnInhibit", [cookie],
                )
        except Exception:
            pass
        self._inhibits = []
        process = self._helper_process
        self._helper_process = None
        if process is not None:
            self._stop_helper(process)
        _log("screen-awake holds released")

    # ── Linux ──────────────────────────────────────────────────────
    def _linux_acquire(self):
        acquired = False

        # 1. The desktop's screensaver service. GNOME/XFCE expose it at
        #    /org/freedesktop/ScreenSaver, KDE at /ScreenSaver — try both.
        for service, path in SCREENSAVER_PATHS:
            cookie = self._dbus_call(
                service, path, service, "Inhibit", [APP_NAME, INHIBIT_REASON],
            )
            if cookie is not None:
                self._inhibits.append((service, path, cookie))
                _log(f"{service} inhibited via {path} (cookie {cookie})")
                acquired = True
                break

        # 2. GNOME's own session inhibitor (idle + suspend). GNOME drives
        #    blanking and auto-suspend from its session idle monitor, so
        #    this is taken in addition to (not only instead of) the
        #    freedesktop screensaver one.
        gnome_cookie = self._dbus_call(
            "org.gnome.SessionManager",
            "/org/gnome/SessionManager",
            "org.gnome.SessionManager",
            "Inhibit",
            [APP_NAME, 0, INHIBIT_REASON,
             GNOME_INHIBIT_SUSPEND | GNOME_INHIBIT_IDLE],
        )
        if gnome_cookie is not None:
            self._inhibits.append(
                ("org.gnome.SessionManager", "/org/gnome/SessionManager", gnome_cookie)
            )
            _log(f"org.gnome.SessionManager inhibited (cookie {gnome_cookie})")
            acquired = True

        # 3. logind inhibitor: blocks suspend for desktops that honour it
        #    (KDE does; GNOME covers suspend through the session inhibit
        #    above). Holding this child process open holds the inhibitor.
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
        if self._helper_process is not None:
            _log("logind idle:sleep inhibitor active")

        if not acquired:
            _log("WARNING: no D-Bus screen inhibit was accepted — the screen "
                 "will likely still blank after the system timeout")

    # thin wrapper so release() reads uniformly on every platform
    @staticmethod
    def _dbus_call(service, path, interface, method, args):
        return _dbus_call(service, path, interface, method, args)

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


if __name__ == "__main__":
    # Standalone self-test for the desktop the app runs on:
    #   python keep_awake.py --check
    # It holds the screen awake for 15 s and says exactly which mechanisms
    # were accepted, so keep-awake problems can be verified in isolation.
    if "--check" in sys.argv:
        _log("self-test: holding the screen awake for 15 seconds …")
        lock = ScreenWakeLock()
        lock.acquire()
        _log(f"active holds: {lock.describe()}")
        time.sleep(15)
        lock.release()
        _log("self-test done — the screen may blank/suspend again now")

