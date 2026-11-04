"""Synchronised multiroom playback between instances of this app.

Every instance with Network Sync enabled:

- listens on a TCP port (default 52345) for connections from other instances,
- finds each configured peer MAC address in the system ARP table and, when
  it is missing there, probes every address on the local /24 subnet once so
  the kernel fills the ARP table with every machine that is awake,
- accepts a connection only after the other side identifies itself with a
  hello handshake, so only real Music Engine instances sync up,
- exchanges small JSON commands (play / pause / resume / seek / stop plus a
  periodic state heartbeat) so either instance can trigger the other, a
  "finished" handshake at the end of each track so synced machines play every
  song to the end before moving on together, and "shuffle" / "queue_next"
  commands so turning shuffle on/off (with the exact seed and base queue, so
  every machine rebuilds the identical order) and queueing a song as next
  happen on all machines at once. "play" broadcasts also carry the sender's
  full play order so machines whose queues drifted apart reconverge at every
  track change.

The engine runs on its own thread and talks to the controller through Qt
signals, so playback stays synced while the window is unfocused.
"""

import errno
import json
import os
import queue
import re
import select
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal


DEFAULT_SYNC_PORT = 52345
HELLO_TIMEOUT = 6.0        # seconds allowed to complete the handshake
PING_INTERVAL = 4.0        # seconds between keepalive pings
PEER_TIMEOUT = 15.0        # seconds of silence before a peer is dropped
CONNECT_RETRY = 5.0         # seconds between dial attempts to one peer
SWEEP_COOLDOWN = 20.0      # minimum seconds between /24 sweeps
SELECT_TICK = 0.5           # select() timeout — the loop's heartbeat
CONNECT_DEADLINE = 3.0      # seconds a non-blocking dial may take


def normalize_mac(value):
    """'AA:BB-CC-DD-EE-FF' -> 'aa:bb:cc:dd:ee:ff' (None when not a MAC)."""
    cleaned = str(value or "").strip().lower().replace("-", ":").replace(" ", ":").replace(".", ":")
    parts = [p for p in cleaned.split(":") if p]
    if len(parts) != 6 or not all(
        len(p) == 2 and all(c in "0123456789abcdef" for c in p) for p in parts
    ):
        return None
    return ":".join(parts)


def local_mac():
    """A stable MAC address identifying this machine (for the handshake)."""
    try:
        if os.path.isdir("/sys/class/net"):
            fallback = None
            for name in sorted(os.listdir("/sys/class/net")):
                if name == "lo":
                    continue
                base = Path("/sys/class/net") / name
                try:
                    mac = normalize_mac((base / "address").read_text().strip())
                except OSError:
                    continue
                if not mac or mac == "00:00:00:00:00:00":
                    continue
                try:
                    state = (base / "operstate").read_text().strip()
                except OSError:
                    state = "unknown"
                if state == "up":
                    return mac
                if fallback is None:
                    fallback = mac
            if fallback:
                return fallback
    except OSError:
        pass
    return normalize_mac("%012x" % uuid.getnode())


def local_ip():
    """This machine's LAN IPv4 address, or None (no packets are sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # just picks a route; sends nothing
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def arp_table():
    """{mac: ip} mapping from the system ARP cache."""
    table = {}
    if os.path.exists("/proc/net/arp"):
        try:
            lines = Path("/proc/net/arp").read_text().splitlines()[1:]
        except OSError:
            lines = []
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            ip, mac = parts[0], normalize_mac(parts[3])
            if mac and mac != "00:00:00:00:00:00" and ip.count(".") == 3:
                table[mac] = ip
    else:
        # Windows and other systems: parse `arp -a` output.
        try:
            out = subprocess.run(
                ["arp", "-a"], capture_output=True, text=True, timeout=5
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for match in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\D+([0-9a-fA-F:-]{17})", out):
            ip, mac = match.group(1), normalize_mac(match.group(2))
            if mac and ip.count(".") == 3:
                table[mac] = ip
    return table


def _probe_subnet(port):
    """Probe every host on the local /24 once so the kernel's ARP table gets
    filled with every machine that is awake. Returns nothing; callers re-read
    the ARP table afterwards."""
    ip = local_ip()
    if not ip or ip.count(".") != 3 or ip.startswith("127."):
        return
    prefix = ip.rsplit(".", 1)[0]

    def probe(host_ip):
        try:
            s = socket.create_connection((host_ip, port), timeout=0.4)
            s.close()
        except OSError:
            # Refused / unreachable both still create an ARP entry, which
            # is all this sweep is after.
            pass

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(probe, [f"{prefix}.{i}" for i in range(1, 255)]))


class _Connection:
    """One TCP link to another instance (handshaking or established)."""

    def __init__(self, sock, initiated_by_self, remote_addr=""):
        self.sock = sock
        self.initiated_by_self = initiated_by_self
        self.remote_addr = remote_addr
        self.target_mac = None      # configured peer this dial is for
        self.peer_mac = None        # known once the hello arrives
        self.peer_name = ""
        self.hello_sent = False
        self.hello_received = False
        self.created = time.monotonic()
        self.last_seen = self.created
        self.last_ping = self.created
        self.recv_buffer = b""
        self.send_buffer = b""


class SyncEngine(QObject):
    """Qt-facing wrapper around the background network thread.

    The controller calls send_command() (thread-safe); remote commands and
    peer status changes arrive on the remote_command / peer_status signals.
    """

    remote_command = Signal(str, dict)   # command name, message payload
    peer_status = Signal(str, str)       # peer MAC, human-readable status

    def __init__(self, peers, port=None, parent=None):
        super().__init__(parent)
        self.own_id = local_mac() or "00:00:00:00:00:00"
        self._thread = _SyncThread(
            self, self.own_id, socket.gethostname() or "music engine",
            peers, int(port or DEFAULT_SYNC_PORT),
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._thread.stop()
        self._thread.wait(3000)

    def send_command(self, command, payload=None):
        self._thread.send_command(command, payload or {})

    def peer_statuses(self):
        return dict(self._thread.peer_status)


class _SyncThread(QThread):
    """Background thread that owns every sync socket.

    One select() loop handles: the listen socket, all peer connections,
    non-blocking dial attempts to configured peers, keepalive pings, and the
    outbound queue fed by the controller.
    """

    def __init__(self, engine, own_mac, own_name, peers, port):
        super().__init__()
        self._engine = engine
        self.own_mac = own_mac
        self.own_name = own_name
        self.peers = [m for m in (normalize_mac(p) for p in peers) if m]
        self.port = port
        self._outbox = queue.Queue()
        self._stop_event = threading.Event()
        self._listen_sock = None
        self._connections = []      # every open _Connection (handshaking or established)
        self._pending = {}          # socket -> _Connection (dial in progress)
        self._next_attempt = {}     # mac -> monotonic deadline for the next dial
        self._arp_cache = {}
        self._arp_read_at = 0.0
        self._sweep_running = False
        self._last_sweep = 0.0
        self._seq = 0
        self.peer_status = {}       # mac -> status string (also read by the UI)

    # ---------- public API (called from the Qt thread) ----------

    def send_command(self, command, payload):
        self._outbox.put((command, payload))

    def stop(self):
        self._stop_event.set()

    # ---------- small helpers ----------

    def _set_status(self, mac, status):
        if not mac:
            return
        if self.peer_status.get(mac) != status:
            self.peer_status[mac] = status
            self._engine.peer_status.emit(mac, status)

    def _established(self, mac):
        return next(
            (c for c in self._connections if c.peer_mac == mac and c.hello_received),
            None,
        )

    def _frame(self, command, payload):
        self._seq += 1
        frame = {
            "type": command,
            "sender": self.own_mac,
            "name": self.own_name,
            "seq": self._seq,
        }
        frame.update(payload or {})
        return (json.dumps(frame) + "\n").encode("utf-8")

    def _queue_send(self, conn, command, payload):
        conn.send_buffer += self._frame(command, payload)

    def _broadcast(self, command, payload):
        data = self._frame(command, payload)
        for conn in list(self._connections):
            if conn.hello_received:
                conn.send_buffer += data

    def _close(self, conn, reason="", quiet=False):
        if conn in self._connections:
            self._connections.remove(conn)
        sock = conn.sock
        self._pending.pop(sock, None)
        try:
            sock.close()
        except OSError:
            pass
        if not quiet and conn.peer_mac and not self._established(conn.peer_mac):
            if reason == "bye":
                status = "offline (peer closed the app)"
            elif reason in ("timeout", "eof", "error"):
                status = "offline (connection lost)"
            else:
                status = "offline"
            self._set_status(conn.peer_mac, status)

    # ---------- main loop ----------

    def run(self):
        self._open_listen_socket()
        while not self._stop_event.is_set():
            now = time.monotonic()
            self._prune_pending(now)
            self._attempt_connects(now)
            self._send_pings(now)
            self._drain_outbox()

            rlist, wlist = [], []
            if self._listen_sock is not None:
                rlist.append(self._listen_sock)
            for conn in self._connections:
                rlist.append(conn.sock)
                if conn.send_buffer:
                    wlist.append(conn.sock)
            for sock in self._pending:
                wlist.append(sock)

            try:
                readable, writable, _ = select.select(rlist, wlist, [], SELECT_TICK)
            except (OSError, ValueError):
                # A socket died mid-loop; prune and carry on.
                for conn in list(self._connections):
                    try:
                        conn.sock.fileno()
                    except OSError:
                        self._close(conn, "error")
                time.sleep(SELECT_TICK)
                continue

            for sock in readable:
                if sock is self._listen_sock:
                    self._accept()
                elif sock in self._pending:
                    self._finish_connect(sock)
                else:
                    conn = next((c for c in self._connections if c.sock is sock), None)
                    if conn:
                        self._recv(conn)
            for sock in writable:
                if sock in self._pending:
                    self._finish_connect(sock)
                else:
                    conn = next((c for c in self._connections if c.sock is sock), None)
                    if conn:
                        self._flush(conn)
        self._close_all()

    def _close_all(self):
        try:
            for sock in list(self._pending):
                try:
                    sock.close()
                except OSError:
                    pass
            self._pending.clear()
            for conn in list(self._connections):
                if conn.hello_received:
                    try:
                        conn.send_buffer += self._frame("bye", {})
                        self._flush(conn)
                    except OSError:
                        pass
                try:
                    conn.sock.close()
                except OSError:
                    pass
            self._connections.clear()
            if self._listen_sock is not None:
                try:
                    self._listen_sock.close()
                except OSError:
                    pass
                self._listen_sock = None
        except OSError:
            pass

    # ---------- listen / accept ----------

    def _open_listen_socket(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            sock.listen(8)
            sock.setblocking(False)
            self._listen_sock = sock
            print(f"[sync] listening on port {self.port}", flush=True)
        except OSError as error:
            # Outgoing dials still work without a listener, as long as the
            # peers accept our connections.
            print(f"[sync] cannot listen on port {self.port}: {error}", flush=True)
            self._listen_sock = None

    def _accept(self):
        while True:
            try:
                sock, addr = self._listen_sock.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            sock.setblocking(False)
            conn = _Connection(sock, initiated_by_self=False, remote_addr=addr[0])
            self._connections.append(conn)
            # The dialing side speaks first; wait for its hello.

    # ---------- finding and dialing peers ----------

    def _attempt_connects(self, now):
        if not self.peers:
            return
        if now - self._arp_read_at > 0.5:
            self._arp_cache = arp_table()
            self._arp_read_at = now
        for mac in self.peers:
            if self._established(mac):
                continue
            if now < self._next_attempt.get(mac, 0):
                continue
            ip = self._arp_cache.get(mac)
            if not ip:
                self._maybe_sweep(now)
                self._set_status(mac, "searching (scanning the network…)")
                self._next_attempt[mac] = now + CONNECT_RETRY
                continue
            self._start_connect(mac, ip, now)

    def _maybe_sweep(self, now):
        if self._sweep_running or now - self._last_sweep < SWEEP_COOLDOWN:
            return
        self._sweep_running = True
        self._last_sweep = now

        def sweep():
            try:
                _probe_subnet(self.port)
            except Exception as error:  # never let the sweeper crash the app
                print(f"[sync] subnet probe failed: {error}", flush=True)
            finally:
                self._sweep_running = False

        threading.Thread(target=sweep, daemon=True, name="sync-arp-sweep").start()

    def _start_connect(self, mac, ip, now):
        self._next_attempt[mac] = now + CONNECT_RETRY
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            err = sock.connect_ex((ip, self.port))
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
            self._set_status(mac, f"offline (unreachable at {ip})")
            return
        if err not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK):
            try:
                sock.close()
            except OSError:
                pass
            # Nothing listens there: the machine is up but the app is not.
            self._set_status(mac, f"offline (app not running at {ip})")
            return
        conn = _Connection(sock, initiated_by_self=True, remote_addr=ip)
        conn.target_mac = mac
        if err == 0:
            self._connected_socket(conn)
        else:
            self._pending[sock] = conn
            self._set_status(mac, f"connecting ({ip})")

    def _connected_socket(self, conn):
        self._connections.append(conn)
        self._queue_send(conn, "hello", {"app": "MusicEngine", "port": self.port})
        conn.hello_sent = True

    def _finish_connect(self, sock):
        conn = self._pending.pop(sock, None)
        if conn is None:
            try:
                sock.close()
            except OSError:
                pass
            return
        try:
            err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        except OSError:
            err = errno.EBADF
        if err == 0:
            self._connected_socket(conn)
        else:
            try:
                sock.close()
            except OSError:
                pass
            if conn.target_mac:
                if err == errno.ECONNREFUSED:
                    self._set_status(conn.target_mac, f"offline (app not running at {conn.remote_addr})")
                else:
                    self._set_status(conn.target_mac, f"offline (unreachable at {conn.remote_addr})")

    def _prune_pending(self, now):
        for sock, conn in list(self._pending.items()):
            if now - conn.created > CONNECT_DEADLINE:
                self._pending.pop(sock, None)
                try:
                    sock.close()
                except OSError:
                    pass
                if conn.target_mac:
                    self._set_status(conn.target_mac, f"offline (unreachable at {conn.remote_addr})")

    # ---------- data flow ----------

    def _drain_outbox(self):
        while True:
            try:
                command, payload = self._outbox.get_nowait()
            except queue.Empty:
                return
            self._broadcast(command, payload)

    def _recv(self, conn):
        try:
            chunk = conn.sock.recv(65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._close(conn, "error")
            return
        if not chunk:
            self._close(conn, "eof")
            return
        conn.recv_buffer += chunk
        conn.last_seen = time.monotonic()
        while b"\n" in conn.recv_buffer:
            line, conn.recv_buffer = conn.recv_buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            self._handle_message(conn, message)

    def _flush(self, conn):
        if not conn.send_buffer:
            return
        try:
            sent = conn.sock.send(conn.send_buffer)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._close(conn, "error")
            return
        conn.send_buffer = conn.send_buffer[sent:]

    def _send_pings(self, now):
        for conn in list(self._connections):
            if not conn.hello_received:
                if now - conn.created > HELLO_TIMEOUT:
                    self._close(conn, "handshake-timeout")
                continue
            if now - conn.last_seen > PEER_TIMEOUT:
                self._close(conn, "timeout")
            elif now - conn.last_ping >= PING_INTERVAL:
                conn.last_ping = now
                self._queue_send(conn, "ping", {})

    # ---------- messages ----------

    def _handle_message(self, conn, message):
        kind = message.get("type")
        if kind == "ping":
            return
        if kind == "bye":
            self._close(conn, "bye")
            return
        if kind == "hello":
            self._handle_hello(conn, message)
            return
        if not conn.hello_received:
            return  # only hello is accepted before the handshake completes
        if kind in (
            "play",
            "pause",
            "resume",
            "seek",
            "stop",
            "state",
            "finished",
            "shuffle",
            "queue_next",
        ):
            self._engine.remote_command.emit(kind, message)

    def _handle_hello(self, conn, message):
        if message.get("app") != "MusicEngine":
            self._close(conn, "not-app")
            if conn.target_mac:
                self._set_status(
                    conn.target_mac,
                    f"handshake failed (not a Music Engine at {conn.remote_addr})",
                )
            return
        peer_mac = normalize_mac(message.get("sender"))
        if not peer_mac or peer_mac not in self.peers:
            self._close(conn, "not-configured")
            return
        if not conn.hello_sent:
            self._queue_send(conn, "hello", {"app": "MusicEngine", "port": self.port})
            conn.hello_sent = True
        self._register(conn, peer_mac, message.get("name") or peer_mac)

    def _register(self, conn, peer_mac, peer_name):
        existing = self._established(peer_mac)
        conn.peer_mac = peer_mac
        conn.peer_name = peer_name
        conn.hello_received = True
        conn.last_seen = time.monotonic()
        conn.last_ping = conn.last_seen
        if existing and existing is not conn:
            # Both sides dialled out, so there are two links between the
            # same pair. Keep the link opened by the machine with the
            # smaller MAC — both sides run this same rule, so they keep the
            # same link and drop the duplicate.
            keep_self_initiated = self.own_mac < peer_mac
            if conn.initiated_by_self == keep_self_initiated:
                self._close(existing, "duplicate", quiet=True)
            else:
                self._close(conn, "duplicate", quiet=True)
                return
        self._set_status(peer_mac, f"connected ({peer_name})")
        print(f"[sync] connected: {peer_name} ({peer_mac})", flush=True)



