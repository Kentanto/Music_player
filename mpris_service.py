"""MPRIS D-Bus bridge for Linux.

Exports the application as an MPRIS media player on the session bus
(org.mpris.MediaPlayer2.musicplayer) so the desktop's media keys, media
tray widgets, and remote control integrations (KDE Connect, playerctl...)
can drive playback even when the app is not focused.

The D-Bus service runs on a background asyncio loop (via dbus_fast) while
commands are delivered to the Qt main loop through Qt signals, which are
safe to emit from any thread.
"""

import asyncio
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

try:
    from dbus_fast import (
        BusType,
        NameFlag,
        PropertyAccess,
        RequestNameReply,
        Variant,
    )
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import (
        ServiceInterface,
        dbus_method,
        dbus_property,
        dbus_signal,
    )
    HAS_MPRIS_DEPS = True
except ImportError:
    HAS_MPRIS_DEPS = False

MPRIS_PATH = "/org/mpris/MediaPlayer2"
BASE_BUS_NAME = "org.mpris.MediaPlayer2.musicplayer"
NO_TRACK_PATH = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"


def _artwork_uri(artwork):
    """Convert a local artwork path or web URL into a URI for mpris:artUrl."""
    if not artwork or not isinstance(artwork, str):
        return ""
    if artwork.startswith(("http://", "https://", "file://")):
        return artwork
    try:
        path = Path(artwork)
        if path.is_file():
            return path.resolve().as_uri()
    except OSError:
        pass
    return ""


class _PlayerState:
    """Plain-attribute state shared between the Qt and D-Bus threads.

    Values are simple built-ins; attribute access is GIL-safe, so the
    D-Bus thread can read them without locking (worst case a client
    briefly observes a slightly stale value).
    """

    def __init__(self):
        self.playback_status = "Stopped"
        self.title = ""
        self.artist = ""
        self.album = ""
        self.art_url = ""
        self.length_us = 0
        self.track_id = NO_TRACK_PATH
        self.track_counter = 0
        self.position_ms = 0
        self.can_play = False
        self.can_pause = False
        self.can_go_next = False
        self.can_go_previous = False
        self.can_seek = False
        self.shuffle = False
        self.volume = 0.0


def _metadata_dict(state):
    """Build the MPRIS Metadata dictionary (a{sv}) from shared state."""
    metadata = {"mpris:trackid": Variant("o", state.track_id)}
    if state.length_us > 0:
        metadata["mpris:length"] = Variant("x", state.length_us)
    if state.title:
        metadata["xesam:title"] = Variant("s", state.title)
    if state.artist:
        metadata["xesam:artist"] = Variant("as", [state.artist])
    if state.album:
        metadata["xesam:album"] = Variant("s", state.album)
    if state.art_url:
        metadata["mpris:artUrl"] = Variant("s", state.art_url)
    return metadata


class _RootInterface(ServiceInterface):
    """The org.mpris.MediaPlayer2 root interface."""

    def __init__(self, bridge):
        super().__init__("org.mpris.MediaPlayer2")
        self._bridge = bridge

    @dbus_method()
    def Raise(self):
        self._bridge.raise_requested.emit()

    @dbus_method()
    def Quit(self):
        self._bridge.quit_requested.emit()

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> 'b':
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> 's':
        return "Music Player"

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> 's':
        return "musicplayer"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> 'as':
        return ["file"]

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> 'as':
        return ["audio/mpeg", "audio/mp4", "audio/flac", "audio/ogg", "audio/wav"]


class _PlayerInterface(ServiceInterface):
    """The org.mpris.MediaPlayer2.Player interface."""

    def __init__(self, bridge, state):
        super().__init__(PLAYER_INTERFACE)
        self._bridge = bridge
        self._state = state

    # ---------- methods ----------
    @dbus_method()
    def Next(self):
        self._bridge.next_requested.emit()

    @dbus_method()
    def Previous(self):
        self._bridge.previous_requested.emit()

    @dbus_method()
    def Pause(self):
        self._bridge.pause_requested.emit()

    @dbus_method()
    def PlayPause(self):
        self._bridge.play_pause_requested.emit()

    @dbus_method()
    def Stop(self):
        self._bridge.stop_requested.emit()

    @dbus_method()
    def Play(self):
        self._bridge.play_requested.emit()

    @dbus_method()
    def Seek(self, offset: 'x'):
        # D-Bus uses microseconds; the app works in milliseconds.
        self._bridge.seek_requested.emit(int(offset) // 1000)

    @dbus_method()
    def SetPosition(self, track_id: 'o', position: 'x'):
        if track_id != self._state.track_id:
            return
        self._bridge.set_position_requested.emit(int(position) // 1000)

    # ---------- signals ----------
    @dbus_signal()
    def Seeked(self, position) -> 'x':
        return position

    # ---------- properties ----------
    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> 's':
        return self._state.playback_status

    @dbus_property(access=PropertyAccess.READ)
    def LoopStatus(self) -> 's':
        return "None"

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> 'd':
        return 1.0

    @dbus_property(access=PropertyAccess.READWRITE)
    def Shuffle(self) -> 'b':
        return self._state.shuffle

    @Shuffle.setter
    def Shuffle(self, value: 'b'):
        self._bridge.shuffle_requested.emit(bool(value))

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> 'a{sv}':
        return _metadata_dict(self._state)

    @dbus_property(access=PropertyAccess.READWRITE)
    def Volume(self) -> 'd':
        return self._state.volume

    @Volume.setter
    def Volume(self, value: 'd'):
        self._bridge.volume_requested.emit(float(value))

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> 'x':
        return max(0, int(self._state.position_ms)) * 1000

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> 'b':
        return self._state.can_go_next

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> 'b':
        return self._state.can_go_previous

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> 'b':
        return self._state.can_play

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> 'b':
        return self._state.can_pause

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> 'b':
        return self._state.can_seek

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> 'b':
        return True

    def emit_seeked(self, position_ms):
        """Broadcast the Seeked signal (must run on the D-Bus loop)."""
        self.Seeked(max(0, int(position_ms)) * 1000)


class _MprisWorker(threading.Thread):
    """Background thread that owns the asyncio loop and the D-Bus connection."""

    def __init__(self, bridge, state):
        super().__init__(daemon=True, name="mpris-dbus")
        self._bridge = bridge
        self._state = state
        self._ready = threading.Event()
        self._stop_event = None  # asyncio.Event, created inside the loop
        self.startup_error = None
        self.bus_name = None
        self.player_interface = None
        self.loop = None

    def run(self):
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self._main())
        except Exception as error:
            self.startup_error = self.startup_error or error
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _main(self):
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()

            root = _RootInterface(self._bridge)
            player = _PlayerInterface(self._bridge, self._state)
            self.player_interface = player
            bus.export(MPRIS_PATH, root)
            bus.export(MPRIS_PATH, player)

            flags = NameFlag.ALLOW_REPLACEMENT | NameFlag.REPLACE_EXISTING
            name = BASE_BUS_NAME
            reply = await bus.request_name(name, flags=flags)
            attempt = 1
            while (
                reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER)
                and attempt < 5
            ):
                attempt += 1
                name = f"{BASE_BUS_NAME}.instance{attempt}"
                reply = await bus.request_name(name, flags=flags)
            if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
                raise RuntimeError("could not acquire an MPRIS bus name")
            self.bus_name = name
        except Exception as error:
            self.startup_error = error
            self._ready.set()
            return

        self._ready.set()
        self._stop_event = asyncio.Event()
        await self._stop_event.wait()
        try:
            bus.disconnect()
        except Exception:
            pass

    def wait_ready(self, timeout):
        return self._ready.wait(timeout)

    def stop(self):
        if self._stop_event is None or self.loop is None:
            return
        try:
            self.loop.call_soon_threadsafe(self._stop_event.set)
        except RuntimeError:
            pass


class MprisService(QObject):
    """Qt-facing bridge for the MPRIS D-Bus service.

    D-Bus method calls arrive as Qt signals (delivered on the Qt main
    loop); the controller pushes player state back through the set_*
    methods (safe to call from the Qt main thread).
    """

    play_pause_requested = Signal()
    play_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    next_requested = Signal()
    previous_requested = Signal()
    raise_requested = Signal()
    quit_requested = Signal()
    shuffle_requested = Signal(bool)
    volume_requested = Signal(float)      # 0.0 - 1.0
    seek_requested = Signal(int)           # relative, milliseconds
    set_position_requested = Signal(int)  # absolute, milliseconds

    def __init__(self, parent=None):
        super().__init__(parent)
        if not HAS_MPRIS_DEPS:
            raise RuntimeError("dbus_fast is not installed")
        self._state = _PlayerState()
        self._worker = _MprisWorker(self, self._state)
        self._worker.start()
        if not self._worker.wait_ready(15):
            self._worker.stop()
            raise RuntimeError("MPRIS service failed to start (timeout)")
        if self._worker.startup_error is not None:
            raise RuntimeError(f"MPRIS service failed to start: {self._worker.startup_error}")
        print(f"[MPRIS] registered as {self._worker.bus_name}", flush=True)

    # ---------- state pushes (called from the Qt main thread) ----------
    def set_playback_status(self, status):
        self._state.playback_status = status
        self._notify({"PlaybackStatus": status})

    def set_track(self, title, artist, artwork=None, album="", length_ms=0):
        state = self._state
        state.track_counter += 1
        state.title = title or ""
        state.artist = artist or ""
        state.album = album or ""
        state.length_us = int(max(0, length_ms)) * 1000
        state.track_id = f"/org/mpris/MediaPlayer2/Track/{state.track_counter}"
        state.art_url = _artwork_uri(artwork)
        self._notify({"Metadata": _metadata_dict(state)})

    def set_track_length(self, length_ms):
        length_us = int(max(0, length_ms)) * 1000
        if length_us <= 0 or length_us == self._state.length_us:
            return
        self._state.length_us = length_us
        self._notify({"Metadata": _metadata_dict(self._state)})

    def clear_track(self):
        state = self._state
        state.title = ""
        state.artist = ""
        state.album = ""
        state.art_url = ""
        state.length_us = 0
        state.track_id = NO_TRACK_PATH
        state.position_ms = 0
        self._notify({"Metadata": _metadata_dict(state)})

    def set_position_ms(self, position_ms):
        # Cheap by design: Position is polled by clients, never announced.
        self._state.position_ms = int(max(0, position_ms))

    def set_capabilities(self, can_play=False, can_pause=False, can_go_next=False,
                         can_go_previous=False, can_seek=False):
        state = self._state
        state.can_play = bool(can_play)
        state.can_pause = bool(can_pause)
        state.can_go_next = bool(can_go_next)
        state.can_go_previous = bool(can_go_previous)
        state.can_seek = bool(can_seek)
        self._notify({
            "CanPlay": state.can_play,
            "CanPause": state.can_pause,
            "CanGoNext": state.can_go_next,
            "CanGoPrevious": state.can_go_previous,
            "CanSeek": state.can_seek,
        })

    def set_shuffle(self, enabled):
        self._state.shuffle = bool(enabled)
        self._notify({"Shuffle": self._state.shuffle})

    def set_volume(self, volume):
        self._state.volume = max(0.0, min(float(volume), 1.0))
        self._notify({"Volume": self._state.volume})

    def emit_seeked(self, position_ms):
        player = self._worker.player_interface
        if player is None or self._worker.loop is None:
            return
        try:
            self._worker.loop.call_soon_threadsafe(player.emit_seeked, position_ms)
        except RuntimeError:
            pass

    def shutdown(self):
        self._worker.stop()
        self._worker.join(timeout=2.0)

    # ---------- internal ----------
    def _notify(self, changed_properties):
        player = self._worker.player_interface
        if player is None or self._worker.loop is None:
            return

        def emit():
            try:
                player.emit_properties_changed(changed_properties, [])
            except Exception as error:
                print(f"[MPRIS] property notification failed: {error}", flush=True)

        try:
            self._worker.loop.call_soon_threadsafe(emit)
        except RuntimeError:
            pass
