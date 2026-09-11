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
import time

from PySide6.QtCore import QThread, Signal


# Standard HDMI-CEC "User Control" codes (from the CEC spec's UI Command
# table), mapped to the signal each one should trigger. Keyed by the hex
# code rather than cec-ctl's printed name, since the code is stable across
# cec-ctl/v4l-utils versions while the printed spelling isn't guaranteed to
# be.
CEC_CODE_ACTIONS = {
    0x00: "select_requested",   # Select / OK
    0x0B: "select_requested",   # Contents Menu (used as OK on some remotes)
    0x0D: "back_requested",     # Exit / Back
    0x01: "navigation:up",
    0x02: "navigation:down",
    0x03: "navigation:left",
    0x04: "navigation:right",
    # --- Transport controls ---
    0x41: "play_pause",         # Volume Up (LG / some vendors use as Play)
    0x44: "play_pause",         # Play
    0x45: "stop_requested",     # Stop
    0x46: "play_pause",         # Pause
    0x47: "previous_track",     # Record (used as Rewind on some remotes)
    0x48: "previous_track",     # Rewind
    0x49: "next_track",         # Fast Forward
    0x4A: "next_track",         # Eject (sometimes mapped to next)
    0x4B: "next_track",         # Skip Forward
    0x4C: "previous_track",     # Skip Backward
    # --- Function Select (One Touch Play) ---
    0x60: "play_pause",         # Play Function
    0x61: "play_pause",         # Pause-Play Function
    0x62: "stop_requested",     # Record Function
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
        self._last_code = None
        self._last_time = 0.0

    @staticmethod
    def available():
        """Return whether CEC support is available on this platform.

        HDMI-CEC remote support is currently Linux-only because it uses
        the Linux kernel CEC API exposed through /dev/cec*.
        """
        if os.name != "posix":
            return False

        return (
            shutil.which("cec-ctl") is not None
            and shutil.which("stdbuf") is not None
        )

    @staticmethod
    def _test_passwordless_sudo(sudo_path, cec_ctl_path, device):
        """Test whether passwordless sudo works for cec-ctl on the given device."""
        try:
            result = subprocess.run(
                [sudo_path, "-n", cec_ctl_path, f"-d{device}", "-S"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            return result.returncode == 0, result.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, str(error)

    def find_connected_device(self):
        """Return the first /dev/cecN that reports a real physical address.

        Uses passwordless sudo to query devices, since --monitor also
        requires root privileges on the Raspberry Pi vc4_hdmi driver.
        """
        cec_ctl_path = shutil.which("cec-ctl")
        if not cec_ctl_path:
            return None

        sudo_path = shutil.which("sudo")
        if not sudo_path:
            print("[CEC] sudo not found; cannot query CEC devices.", flush=True)
            return None

        # Verify passwordless sudo is available before scanning.
        ok, err = self._test_passwordless_sudo(sudo_path, cec_ctl_path, "/dev/cec0")
        if not ok:
            print(
                "[CEC] passwordless sudo not available for cec-ctl; "
                "cannot auto-detect CEC devices.",
                flush=True,
            )
            print(f"[CEC] sudo error: {err}", flush=True)
            return None

        for dev in sorted(glob.glob("/dev/cec*")):
            try:
                result = subprocess.run(
                    [sudo_path, "-n", cec_ctl_path, f"-d{dev}", "-S"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    continue
                match = _PHYS_ADDR_RE.search(result.stdout)
                if match and match.group(1).lower() != "f.f.f.f":
                    print(
                        f"[CEC] Found CEC device {dev} at {match.group(1)}.",
                        flush=True,
                    )
                    return dev, match.group(1)
            except (OSError, subprocess.TimeoutExpired):
                continue

        print("[CEC] No connected CEC device found.", flush=True)
        return None

    def run(self):
        # HDMI-CEC remote support currently only applies to Linux.
        # On Windows, simply do nothing so the rest of the application
        # continues normally.
        if os.name != "posix":
            print(
                "[CEC] HDMI-CEC remote support is disabled on this platform.",
                flush=True,
            )
            return

        if not self.available():
            print(
                "[CEC] cec-ctl/stdbuf not available; "
                "CEC remote support is disabled.",
                flush=True,
            )
            return

        device = self._device
        phys_addr = self._phys_addr

        if not device or not phys_addr:
            found = self.find_connected_device()
            if not found:
                print(
                    "[CEC] No usable HDMI-CEC device found.",
                    flush=True,
                )
                return

            device = device or found[0]
            phys_addr = phys_addr or found[1]

        # Resolve exact binary paths.
        cec_ctl_path = shutil.which("cec-ctl") or "/usr/bin/cec-ctl"
        stdbuf_path = shutil.which("stdbuf") or "/usr/bin/stdbuf"
        sudo_path = shutil.which("sudo")

        if not sudo_path:
            print(
                "[CEC] ERROR: sudo is required for "
                "cec-ctl --monitor on this Linux system.",
                flush=True,
            )
            return

        # Build cec-ctl command.
        cec_cmd = [
            cec_ctl_path,
            f"-d{device}",
            "--playback",
            "--to", "0",
            "--active-source", f"phys-addr={phys_addr}",
            "--monitor",
        ]

        # The Raspberry Pi vc4_hdmi driver requires root privileges for
        # --monitor, even though normal users can access /dev/cec*.
        #
        # Check that passwordless sudo is available before starting the
        # monitor. This prevents sudo from hanging while waiting for a
        # password inside the Qt application.
        ok, err = self._test_passwordless_sudo(sudo_path, cec_ctl_path, device)
        if not ok:
            print(
                "[CEC] ERROR: passwordless sudo is not available "
                "for cec-ctl.",
                flush=True,
            )
            print(
                "[CEC] Configure sudoers with:",
                flush=True,
            )
            print(
                f"[CEC]   {getpass.getuser()} ALL=(ALL) "
                f"NOPASSWD: {cec_ctl_path}",
                flush=True,
            )
            print(f"[CEC] sudo error: {err}", flush=True)
            return

        # Only cec-ctl runs as root.
        cec_cmd = [sudo_path, "-n"] + cec_cmd

        # stdbuf remains unprivileged and makes cec-ctl output line-buffered.
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

        # Button released — do NOT clear the debounce here.
        # Some TVs send a spurious second PRESSED event right after RELEASE
        # for a single physical tap; keeping _last_code set prevents that
        # from double-firing until the debounce window expires.
        if "USER_CONTROL_RELEASED" in line:
            return

        # Detect permission error (fallback if sudo check above somehow missed it)
        low = line_stripped.lower()
        if "monitor mode failed" in low or "run this as root" in low or "permission denied" in low:
            cec_ctl_path = shutil.which("cec-ctl") or "/usr/bin/cec-ctl"
            who = getpass.getuser()
            print(
                "[CEC] WARNING: cec-ctl needs root permissions for --monitor mode.\n"
                "[CEC]          Passwordless sudo should have been checked before starting.\n"
                "[CEC]          If you see this, the sudoers rule may have changed.\n"
                "[CEC]          Fix: add this to /etc/sudoers (use visudo):\n"
                f"[CEC]             {who} ALL=(ALL) NOPASSWD: {cec_ctl_path}\n"
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

        # Debounce: TVs repeat a PRESSED event very quickly even for a
        # single tap (or while a button is held). Ignore repeats of the
        # same code within 500 ms to prevent double-firing navigation
        # and play/pause toggles.
        now = time.monotonic()
        if code == self._last_code and (now - self._last_time) < 0.5:
            return
        self._last_code = code
        self._last_time = now

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
