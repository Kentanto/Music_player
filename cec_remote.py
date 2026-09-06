"""Linux HDMI-CEC remote support for Raspberry Pi installations.

Uses `cec-ctl` (part of v4l-utils, the maintained tool for the Linux kernel
CEC API) rather than `cec-client`/libcec, which is largely unmaintained and
doesn't reliably work against the Pi's kernel CEC driver.

Two things `cec-client` never had to worry about, that `cec-ctl` requires
explicitly:

1. Active Source: registering as a CEC device isn't enough for the TV to
   route remote button presses here -- it also needs to be told this
   device is the active source for its physical HDMI address. Without
   this, `cec-ctl --monitor` will run but never see key events.
2. Output buffering: `cec-ctl` fully buffers its stdout when it isn't a
   tty (i.e. when piped, as here), so without `stdbuf -oL` you may see no
   output at all until its internal buffer fills or the process exits.
"""

import glob
import re
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal


# Standard HDMI-CEC "User Control" codes (from the CEC spec's UI Command
# table), mapped to the signal each one should trigger. Keyed by the hex
# code rather than cec-ctl's printed name, since the code is stable across
# cec-ctl/v4l-utils versions while the printed spelling isn't guaranteed to
# be.
CEC_CODE_ACTIONS = {
    0x00: "select_requested",   # Select / OK
    0x0D: "back_requested",     # Exit / Back
    0x01: "navigation:up",
    0x02: "navigation:down",
    0x03: "navigation:left",
    0x04: "navigation:right",
    0x44: "play_pause",         # Play
    0x46: "play_pause",         # Pause
    0x45: "stop_requested",     # Stop
    0x4B: "next_track",         # Forward/skip-forward
    0x4C: "previous_track",     # Backward/skip-backward
}

# Matches a "ui-cmd: <name> (0x44)" line, which cec-ctl only prints for
# USER_CONTROL_PRESSED messages (USER_CONTROL_RELEASED has no such line),
# so this alone is enough to identify a key press.
_UI_CMD_RE = re.compile(r"ui-cmd:.*\(0x([0-9A-Fa-f]{1,2})\)")

# Matches the "Physical Address" line from `cec-ctl -S` output, used for
# auto-detecting which /dev/cecN is actually wired to the connected port.
_PHYS_ADDR_RE = re.compile(r"Physical Address\s*:\s*([0-9a-fA-F.]+)")


class CecRemoteListener(QThread):
    """Read cec-ctl key events and forward supported controls to the UI."""

    play_pause = Signal()
    next_track = Signal()
    previous_track = Signal()
    stop_requested = Signal()
    back_requested = Signal()
    navigation = Signal(str)
    select_requested = Signal()

    def __init__(self, parent=None, *, device: str | None = None,
                 phys_addr: str | None = None):
        """
        parent: standard Qt parent (matches QThread convention).
        device: e.g. "/dev/cec1". If omitted, auto-detected (see
            find_connected_device()).
        phys_addr: e.g. "2.0.0.0". If omitted, auto-detected from the
            chosen device's own reported Physical Address.
        """
        super().__init__(parent)
        self._device = device
        self._phys_addr = phys_addr
        self.process = None

    @staticmethod
    def available():
        """Return whether cec-ctl (and stdbuf) are installed."""
        return shutil.which("cec-ctl") is not None and shutil.which("stdbuf") is not None

    @staticmethod
    def find_connected_device():
        """Return the first /dev/cecN that reports a real physical address.

        Raspberry Pi 5 (and some other boards) expose one /dev/cecN per
        HDMI output, but only the one that's actually plugged in and has
        successfully parsed the TV's EDID will report a valid physical
        address -- the others sit at "f.f.f.f" forever. Returns None if
        none are found (or cec-ctl isn't installed).
        """
        if not shutil.which("cec-ctl"):
            return None
        for dev in sorted(glob.glob("/dev/cec*")):
            try:
                result = subprocess.run(
                    ["cec-ctl", f"-d{dev}", "-S"],
                    capture_output=True, text=True, timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            match = _PHYS_ADDR_RE.search(result.stdout)
            if match and match.group(1).lower() != "f.f.f.f":
                return dev, match.group(1)
        return None

    def run(self):
        if not self.available():
            return

        device = self._device
        phys_addr = self._phys_addr
        if not device or not phys_addr:
            found = self.find_connected_device()
            if not found:
                return
            device = device or found[0]
            phys_addr = phys_addr or found[1]

        cmd = [
            "stdbuf", "-oL", "-eL",
            "cec-ctl", f"-d{device}",
            "--playback",
            "--to", "0", "--active-source", f"phys-addr={phys_addr}",
            "--monitor",
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
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
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                self.process = None

    def _handle_line(self, line):
        line_stripped = line.strip()
        print(f"[CEC-RAW] {line_stripped}", flush=True)

        # Detect permission error
        if "monitor mode failed" in line_stripped.lower() or "run this as root" in line_stripped.lower():
            print(
                "[CEC] WARNING: cec-ctl needs root permissions for --monitor mode.\n"
                "[CEC]          Run with: sudo python main.py\n"
                "[CEC]          Or add user to video group: sudo usermod -aG video $USER",
                flush=True,
            )
            return

        match = _UI_CMD_RE.search(line)
        if not match:
            return

        code = int(match.group(1), 16)
        action = CEC_CODE_ACTIONS.get(code)
        print(
            f"[CEC] parsed code=0x{code:02X} action={action or 'ignored'}",
            flush=True,
        )
        if action:
            if action.startswith("navigation:"):
                print(f"[CEC-EMIT] navigation('{action.split(':', 1)[1]}')", flush=True)
                self.navigation.emit(action.split(":", 1)[1])
            else:
                print(f"[CEC-EMIT] {action}()", flush=True)
                getattr(self, action).emit()

    def stop(self):
        self.requestInterruption()
        if self.process:
            self.process.terminate()
        self.wait()
