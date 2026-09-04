"""Linux HDMI-CEC remote support for Raspberry Pi installations."""

import shutil
import subprocess

from PySide6.QtCore import QThread, Signal


class CecRemoteListener(QThread):
    """Read libCEC key events and forward supported controls to the UI."""

    play_pause = Signal()
    next_track = Signal()
    previous_track = Signal()
    stop_requested = Signal()
    navigation = Signal(str)
    select_requested = Signal()

    KEY_ACTIONS = {
        "play": "play_pause",
        "pause": "play_pause",
        "play/pause": "play_pause",
        "next": "next_track",
        "next track": "next_track",
        "previous": "previous_track",
        "previous track": "previous_track",
        "stop": "stop_requested",
        "up": "navigation:up",
        "down": "navigation:down",
        "left": "navigation:left",
        "right": "navigation:right",
        "select": "select_requested",
        "enter": "select_requested",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None

    @staticmethod
    def available():
        """Return whether the libCEC command-line listener is installed."""
        return shutil.which("cec-client") is not None

    def run(self):
        if not self.available():
            return

        try:
            self.process = subprocess.Popen(
                ["cec-client", "-d", "1"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in self.process.stdout:
                if self.isInterruptionRequested():
                    break
                self._handle_line(line)
        except (OSError, ValueError):
            pass
        finally:
            if self.process:
                self.process.terminate()
                self.process.wait(timeout=3)
                self.process = None

    def _handle_line(self, line):
        marker = "key pressed:"
        lowered = line.casefold()
        marker_index = lowered.find(marker)
        if marker_index < 0:
            return

        key_name = line[marker_index + len(marker):].strip().casefold()
        action = self.KEY_ACTIONS.get(key_name)
        if action:
            if action.startswith("navigation:"):
                self.navigation.emit(action.split(":", 1)[1])
            else:
                getattr(self, action).emit()

    def stop(self):
        self.requestInterruption()
        if self.process:
            self.process.terminate()
        self.wait()