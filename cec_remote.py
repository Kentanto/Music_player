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
import os
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
        self._use_sudo = False

    @staticmethod
    def available():
        """Return whether cec-ctl (and stdbuf) are installed."""
        return shutil.which("cec-ctl") is not None and shutil.which("stdbuf") is not None

    @staticmethod
    def _sudo_available():
        return shutil.which("sudo") is not None

    def _query_device(self, dev, use_sudo=False):
        """Run `cec-ctl -d{dev} -S`, optionally with sudo."""
        cmd = (["sudo", "-n"] if use_sudo else []) + ["cec-ctl", f"-d{dev}", "-S"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result

    def find_connected_device(self):
        """Return the first /dev/cecN that reports a real physical address.

        Tries plain cec-ctl first, then sudo -n cec-ctl if permission denied.
        """
        if not shutil.which("cec-ctl"):
            return None
        can_sudo = self._sudo_available()
        for dev in sorted(glob.glob("/dev/cec*")):
            for use_sudo in (False, True):
                if use_sudo and not can_sudo:
                    continue
                result = self._query_device(dev, use_sudo=use_sudo)
                if result is None:
                    continue
                match = _PHYS_ADDR_RE.search(result.stdout)
                if match and match.group(1).lower() != "f.f.f.f":
                    if use_sudo:
                        self._use_sudo = True
                        print(
                            f"[CEC] {dev} needs sudo; only cec-ctl will run elevated.",
                            flush=True,
                        )
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

        # Build cec-ctl command, with or without sudo depending on what worked
        cec_cmd = ["cec-ctl", f"-d{device}",
                   "--playback",
                   "--to", "0", "--active-source", f"phys-addr={phys_addr}",
                   "--monitor"]

        if self._use_sudo:
            # Sanity check: confirm sudo -n works non-interactively
            test = subprocess.run(
                ["sudo", "-n", "cec-ctl", f"-d{device}", "-S"],
                capture_output=True, text=True, timeout=5,
            )
            if test.returncode != 0 and ("password" in test.stderr.lower()
                                          or "password" in test.stdout.lower()):
                print(
                    "[CEC] ERROR: cec-ctl needs sudo but 'sudo -n cec-ctl' is asking for a password.\n"
                    "[CEC] Add this line to /etc/sudoers (use visudo):\n"
                    f"[CEC]   {os.getlogin()} ALL=(ALL) NOPASSWD: /usr/bin/cec-ctl, /usr/bin/stdbuf\n"
                    "[CEC] Then restart the app.",
                    flush=True,
                )
                return
            cec_cmd = ["sudo", "-n"] + cec_cmd

        cmd = ["stdbuf", "-oL", "-eL"] + cec_cmd

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

        # Detect permission error (fallback if auto-detection missed it)
        if "monitor mode failed" in line_stripped.lower() or "run this as root" in line_stripped.lower():
            print(
                "[CEC] WARNING: cec-ctl needs root permissions for --monitor mode.\n"
                "[CEC]          Run the app normally and add this line to /etc/sudoers:\n"
                f"[CEC]          {os.getlogin()} ALL=(ALL) NOPASSWD: /usr/bin/cec-ctl, /usr/bin/stdbuf\n"
                "[CEC]          Then restart the app.",
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
