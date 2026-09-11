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

import getpass
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
    0x0B: "select_requested",   # Tune Function (common alternate OK)
    0x41: "select_requested",   # Play Function (common alternate OK)
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
# Some versions print "UI Command:" or use different spacing.
_UI_CMD_RE = re.compile(r"ui[- ]cmd:.*\(0x([0-9A-Fa-f]{1,2})\)", re.IGNORECASE)

# Fallback: some older versions of v4l-utils print "0x44" in the payload
# or on a separate line after USER_CONTROL_PRESSED.
_UI_HEX_RE = re.compile(r"0x([0-9A-Fa-f]{1,2})(?:\s|$|,)")

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

    @staticmethod
    def _pkexec_available():
        return shutil.which("pkexec") is not None

    def _query_device(self, dev, method="plain"):
        """Run `cec-ctl -d{dev} -S` with plain, sudo, or pkexec elevation."""
        base = ["cec-ctl", f"-d{dev}", "-S"]
        if method == "sudo":
            cmd = ["sudo", "-n"] + base
        elif method == "pkexec":
            cmd = ["pkexec"] + base
        else:
            cmd = base
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result

    def find_connected_device(self):
        """Return the first /dev/cecN that reports a real physical address.

        Tries plain cec-ctl first, then sudo -n, then pkexec.
        """
        if not shutil.which("cec-ctl"):
            return None
        can_sudo = self._sudo_available()
        can_pkexec = self._pkexec_available()
        for dev in sorted(glob.glob("/dev/cec*")):
            for method in ("plain", "sudo", "pkexec"):
                if method == "sudo" and not can_sudo:
                    continue
                if method == "pkexec" and not can_pkexec:
                    continue
                result = self._query_device(dev, method=method)
                if result is None:
                    continue
                match = _PHYS_ADDR_RE.search(result.stdout)
                if match and match.group(1).lower() != "f.f.f.f":
                    if method == "sudo":
                        self._use_sudo = True
                        print(
                            f"[CEC] {dev} needs sudo; cec-ctl will run with sudo.",
                            flush=True,
                        )
                    elif method == "pkexec":
                        self._use_sudo = "pkexec"
                        print(
                            f"[CEC] {dev} needs elevation; cec-ctl will run with pkexec.",
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

        # Resolve exact binary paths for helpful error messages
        cec_ctl_path = shutil.which("cec-ctl") or "/usr/bin/cec-ctl"
        stdbuf_path = shutil.which("stdbuf") or "/usr/bin/stdbuf"
        sudo_path = shutil.which("sudo")
        who = getpass.getuser()

        # Build cec-ctl command.
        # The Raspberry Pi vc4_hdmi driver allows normal users to query
        # the adapter, but requires elevated privileges for --monitor.
        cec_cmd = [
            cec_ctl_path,
            f"-d{device}",
            "--playback",
            "--to", "0",
            "--active-source", f"phys-addr={phys_addr}",
            "--monitor",
        ]

        # cec-ctl --monitor requires root on this system.
        # Check whether passwordless sudo is available independently of
        # whether normal `cec-ctl -S` access worked.
        if self._use_sudo != "pkexec" and sudo_path:
            try:
                sudo_test = subprocess.run(
                    [sudo_path, "-n", cec_ctl_path, f"-d{device}", "-S"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )

                if sudo_test.returncode == 0:
                    self._use_sudo = True
                    print(
                        "[CEC] Passwordless sudo available; "
                        "using elevated cec-ctl for monitor mode.",
                        flush=True,
                    )
                else:
                    self._use_sudo = False

            except (OSError, subprocess.TimeoutExpired):
                self._use_sudo = False

        if self._use_sudo is True:
            # Run only cec-ctl as root. stdbuf remains unprivileged.
            cec_cmd = [sudo_path, "-n"] + cec_cmd

        elif self._use_sudo == "pkexec":
            cec_cmd = ["pkexec"] + cec_cmd

        else:
            print(
                "[CEC] ERROR: cec-ctl --monitor requires elevated privileges "
                "on this system, but passwordless sudo is not available.\n"
                "[CEC] Add this line with 'sudo visudo':\n"
                f"[CEC]   {who} ALL=(ALL) NOPASSWD: {cec_ctl_path}\n"
                "[CEC] Then restart the application.",
                flush=True,
            )
            return

        # stdbuf stays outside sudo so only cec-ctl receives elevation.
        cmd = [stdbuf_path, "-oL", "-eL"] + cec_cmd

        print(
            f"[CEC] Starting monitor on {device} "
            f"(physical address {phys_addr}).",
            flush=True,
        )

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

        except (OSError, ValueError) as error:
            print(
                f"[CEC] Failed to start cec-ctl monitor: {error}",
                flush=True,
            )

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
        low = line_stripped.lower()
        if "monitor mode failed" in low or "run this as root" in low or "permission denied" in low:
            cec_ctl_path = shutil.which("cec-ctl") or "/usr/bin/cec-ctl"
            stdbuf_path = shutil.which("stdbuf") or "/usr/bin/stdbuf"
            who = getpass.getuser()
            print(
                "[CEC] WARNING: cec-ctl needs root permissions for --monitor mode.\n"
                "[CEC]          Options (pick one):\n"
                "[CEC]          1) Install pkexec so the app can auto-elevate just cec-ctl.\n"
                "[CEC]          2) Add this to /etc/sudoers (use visudo):\n"
                f"[CEC]             {who} ALL=(ALL) NOPASSWD: {cec_ctl_path}, {stdbuf_path}\n"
                "[CEC]          3) Create a udev rule so your user can access /dev/cec*.\n"
                "[CEC]          Then restart the app.",
                flush=True,
            )
            return

        match = _UI_CMD_RE.search(line)
        if match:
            code = int(match.group(1), 16)
        else:
            # If there's no ui-cmd line, look for a lone hex code on a
            # USER_CONTROL_PRESSED line (avoids matching addresses).
            if "USER_CONTROL_PRESSED" not in line:
                return
            fm = _UI_HEX_RE.search(line)
            if not fm:
                return
            code = int(fm.group(1), 16)

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
