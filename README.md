Excellent. We can now say with certainty that sudo is not the problem.

This command works without a password:

sudo -n /usr/bin/cec-ctl --device /dev/cec1 --monitor

And your sudo permissions are actually more than sufficient:

(ALL) NOPASSWD: ALL
(root) NOPASSWD: /usr/bin/cec-ctl

So the application is doing something different from the command that works.
I see the actual problem in your Python code

Your code builds:

cec_cmd = [
    cec_ctl_path,
    f"-d{device}",
    "--playback",
    "--to", "0",
    "--active-source", f"phys-addr={phys_addr}",
    "--monitor",
]

Then wraps it:

cmd = [stdbuf_path, "-oL", "-eL"] + cec_cmd

When sudo is enabled, it becomes:

stdbuf -oL -eL sudo -n /usr/bin/cec-ctl ...

But your sudoers configuration may interact badly with the use_pty setting when launched from Qt/QThread/Popen.

More importantly, your application's _use_sudo detection is unnecessarily complicated and can still leave _use_sudo false.

We can remove that entire uncertainty.
I recommend making run() explicitly use sudo on Linux

You've established that your machine requires it, so don't try to dynamically determine it.

Replace your run() with this:
CEC run() — fixed sudo handling

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

        # Resolve exact binary paths.
        cec_ctl_path = shutil.which("cec-ctl") or "/usr/bin/cec-ctl"
        stdbuf_path = shutil.which("stdbuf") or "/usr/bin/stdbuf"
        sudo_path = shutil.which("sudo")

        if not sudo_path:
            print(
                "[CEC] ERROR: sudo is required for cec-ctl --monitor "
                "on this system, but sudo was not found.",
                flush=True,
            )
            return

        # cec-ctl --monitor requires root privileges with the Raspberry Pi
        # vc4_hdmi driver. We already know passwordless sudo works on this
        # system, so explicitly use sudo for the monitor process.
        cec_cmd = [
            cec_ctl_path,
            f"-d{device}",
            "--playback",
            "--to", "0",
            "--active-source", f"phys-addr={phys_addr}",
            "--monitor",
        ]

        # Verify passwordless sudo before starting the monitor.
        try:
            sudo_test = subprocess.run(
                [
                    sudo_path,
                    "-n",
                    cec_ctl_path,
                    f"-d{device}",
                    "-S",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )

            if sudo_test.returncode != 0:
                print(
                    "[CEC] ERROR: passwordless sudo for cec-ctl is not "
                    "available.\n"
                    f"[CEC] sudo error: {sudo_test.stderr.strip()}",
                    flush=True,
                )
                return

        except (OSError, subprocess.TimeoutExpired) as error:
            print(
                f"[CEC] ERROR: could not test sudo for cec-ctl: {error}",
                flush=True,
            )
            return

        # Only cec-ctl runs as root. stdbuf remains unprivileged.
        cec_cmd = [sudo_path, "-n"] + cec_cmd

        cmd = [stdbuf_path, "-oL", "-eL"] + cec_cmd

        print(
            f"[CEC] Starting elevated CEC monitor: "
            f"{cec_ctl_path} on {device}, "
            f"physical address {phys_addr}",
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

But there's one thing I want you to test first

Before changing the application, run the exact command structure that Python will use:

/usr/bin/stdbuf -oL -eL sudo -n /usr/bin/cec-ctl -d/dev/cec1 --playback --to 0 --active-source phys-addr=2.0.0.0 --monitor

If that works, leave it running and press a button on the TV remote.

You should see:

USER_CONTROL_PRESSED

Then Ctrl+C.
Why this test matters

Your standalone test was:

sudo -n /usr/bin/cec-ctl --device /dev/cec1 --monitor

But the application actually uses:

stdbuf → sudo → cec-ctl

and additionally passes:

--playback
--to 0
--active-source ...
--monitor

So we need to test the exact invocation, rather than assuming those combinations behave identically.

If the exact command above works, the updated Python code should work too.

If it doesn't work, paste the output — because then we've isolated the issue to stdbuf, the command ordering/options, or the process environment rather than sudo itself.
You said:
ChatGPT said:

Yes — we should keep it cross-platform. I would not use the last version I gave you as-is, because it unconditionally requires sudo, which would break Windows.

Your original code already has the right idea: Linux needs special handling, while Windows should continue using the existing cec-ctl behavior if available.

The clean approach is:

    Linux/Raspberry Pi: automatically use passwordless sudo for cec-ctl --monitor.
    Windows: don't invoke sudo at all.
    Other platforms: don't invoke sudo unless explicitly supported.
    Keep stdbuf only where it exists.
    Keep your automatic device detection.

I would modify your run() like this:
Cross-platform CEC run()

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

        # Resolve exact binary paths.
        cec_ctl_path = shutil.which("cec-ctl") or "cec-ctl"
        stdbuf_path = shutil.which("stdbuf")
        sudo_path = shutil.which("sudo")
        who = getpass.getuser()

        # Build the cec-ctl command.
        cec_cmd = [
            cec_ctl_path,
            f"-d{device}",
            "--playback",
            "--to", "0",
            "--active-source", f"phys-addr={phys_addr}",
            "--monitor",
        ]

        # ------------------------------------------------------------------
        # Linux / Raspberry Pi
        #
        # The vc4_hdmi kernel CEC driver allows normal users to access the
        # adapter, but requires root privileges for --monitor.
        #
        # Windows and other platforms must NOT go through sudo.
        # ------------------------------------------------------------------
        if os.name == "posix":
            if sudo_path:
                try:
                    # Check whether cec-ctl can be run through sudo without
                    # prompting for a password.
                    sudo_test = subprocess.run(
                        [
                            sudo_path,
                            "-n",
                            cec_ctl_path,
                            f"-d{device}",
                            "-S",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5,
                    )

                    if sudo_test.returncode == 0:
                        cec_cmd = [sudo_path, "-n"] + cec_cmd
                        print(
                            "[CEC] Using passwordless sudo for "
                            "cec-ctl --monitor.",
                            flush=True,
                        )
                    else:
                        print(
                            "[CEC] ERROR: cec-ctl --monitor requires "
                            "elevated privileges on this system, but "
                            "passwordless sudo is unavailable.",
                            flush=True,
                        )
                        print(
                            "[CEC] Add this with 'sudo visudo':",
                            flush=True,
                        )
                        print(
                            f"[CEC]   {who} ALL=(ALL) NOPASSWD: "
                            f"{cec_ctl_path}",
                            flush=True,
                        )
                        return

                except (OSError, subprocess.TimeoutExpired) as error:
                    print(
                        f"[CEC] ERROR: could not check sudo: {error}",
                        flush=True,
                    )
                    return
            else:
                print(
                    "[CEC] ERROR: sudo is required for "
                    "cec-ctl --monitor on this Linux system.",
                    flush=True,
                )
                return

        # ------------------------------------------------------------------
        # Windows / other platforms
        #
        # Do not use sudo. If cec-ctl is available, run it directly.
        # ------------------------------------------------------------------

        # stdbuf is a Linux/Unix utility and is not normally available on
        # Windows. Only use it when it actually exists.
        if stdbuf_path:
            cmd = [stdbuf_path, "-oL", "-eL"] + cec_cmd
        else:
            cmd = cec_cmd

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

One important correction

Your available() currently says:

return shutil.which("cec-ctl") is not None and shutil.which("stdbuf") is not None

That's not Windows-compatible, because stdbuf generally isn't present on Windows.

Change it to:
Cross-platform available()

    @staticmethod
    def available():
        """Return whether cec-ctl is installed.

        stdbuf is optional because it is normally unavailable on Windows.
        """
        return shutil.which("cec-ctl") is not None

That gives you:
Platform	cec-ctl	stdbuf	sudo
Raspberry Pi/Linux	Required	Used if available	Used for monitor
Windows	Required	Optional	Never used
Other Unix	Required	Used if available	Used for monitor

One caveat: Windows itself generally doesn't have the Linux kernel CEC interface or /dev/cec1, so if your Windows version is using a different CEC mechanism/hardware, we shouldn't force this Linux device-detection logic onto it. If your Windows machine actually has cec-ctl and a compatible CEC adapter, this code won't add Linux-specific sudo behavior.

Also, your find_connected_device() currently has Linux-specific assumptions around /dev/cec*. If the Windows version already works today, I would leave that part alone for now rather than accidentally breaking it.

So the safest next move is to apply the two changes above to run() and available(), while preserving the rest of your class.
You said:
ChatGPT said:

Exactly. In that case, I'd make the CEC listener Linux-only but completely harmless on Windows.

The key is to have run() immediately return on non-Linux platforms. That means Windows won't try cec-ctl, /dev/cec*, sudo, stdbuf, etc.

I'd make these two changes.
1. Replace available()
Cross-platform available()

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

2. Keep run() Linux-only

Then use this version:
Linux-only CEC run()

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
        try:
            sudo_test = subprocess.run(
                [
                    sudo_path,
                    "-n",
                    cec_ctl_path,
                    f"-d{device}",
                    "-S",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )

            if sudo_test.returncode != 0:
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
                return

        except (OSError, subprocess.TimeoutExpired) as error:
            print(
                f"[CEC] ERROR: could not check sudo: {error}",
                flush=True,
            )
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

Result

On your Raspberry Pi:

Linux
  ↓
CEC enabled
  ↓
cec-ctl detected
  ↓
sudo -n cec-ctl --monitor
  ↓
TV remote works

On Windows:

Windows
  ↓
available() → False
  ↓
CEC listener does nothing
  ↓
Music Engine continues normally

So you won't get Windows errors about /dev/cec*, cec-ctl, sudo, or stdbuf.

One small note: your existing find_connected_device() can stay as-is because run() never calls it on Windows.
