from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
try:
    from PySide6.QtMultimedia import QAudioBufferOutput
except ImportError:
    QAudioBufferOutput = None
from PySide6.QtCore import QUrl, QTimer, Signal, QObject, QThread
import os
import random
import time

from cache import resolve_stream, probe_stream_url, cached_stream_path


class PlayerSignals(QObject):
    """Signals for player events"""
    position_changed = Signal(int)  # Current position in ms
    duration_changed = Signal(int)  # Duration in ms
    state_changed = Signal(object)  # QMediaPlayer state enum
    track_ended = Signal()  # Emitted when track finishes naturally
    autoplay_next = Signal()  # Signal to controller to play next
    audio_buffer_received = Signal(object)


class StreamResolveWorker(QThread):
    """Downloads a remote stream off the UI thread so playing a YouTube URL
    never freezes the app (same pattern as SearchWorker / MetadataFetcher).

    First emits `streaming` with a direct network audio URL so playback can
    start immediately, then downloads the full file to the local cache and
    emits `resolved` with its path so the player can swap over without
    losing its position."""

    streaming = Signal(int, str, str)  # generation, original url, network stream url
    resolved = Signal(int, str, object)  # generation, original url, local path/URL

    def __init__(self, url, generation, parent=None):
        super().__init__(parent)
        self._url = url
        self._generation = generation

    def run(self):
        # Already downloaded? Hand back the local file straight away — no
        # network round-trip needed.
        cached = cached_stream_path(self._url)
        if cached:
            self.resolved.emit(self._generation, self._url, cached)
            return

        # Quick probe: get the direct audio URL so the player can start
        # streaming right away...
        stream_url = probe_stream_url(self._url)
        if stream_url:
            self.streaming.emit(self._generation, self._url, stream_url)
            # ...while the full file downloads to the cache in this same
            # thread.  The probe already validated the duration, so the
            # duplicate probe pass is skipped.
            local = resolve_stream(self._url, probe=False)
            self.resolved.emit(self._generation, self._url, local)
            return

        # Probe failed — fall back to the plain download-only path.
        local = resolve_stream(self._url)
        self.resolved.emit(self._generation, self._url, local)


class Player:
    def __init__(self):
        self.player = QMediaPlayer()
        self._media_devices = QMediaDevices()
        self.audio = QAudioOutput(self._media_devices.defaultAudioOutput())
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self.signals = PlayerSignals()

        self.player.setAudioOutput(self.audio)
        self.audio_buffer_output = None
        if QAudioBufferOutput is not None and hasattr(self.player, "setAudioBufferOutput"):
            self.audio_buffer_output = QAudioBufferOutput(self.player)
            self.player.setAudioBufferOutput(self.audio_buffer_output)
            self.audio_buffer_output.audioBufferReceived.connect(
                self.signals.audio_buffer_received.emit
            )

        self.queue = []
        self.index = -1
        self.autoplay_enabled = True
        self.shuffle_enabled = False
        self._base_queue = []
        self._ordered_queue = []
        self._current_item = None
        # The source that is ACTUALLY playing.  _current_item doubles as the
        # queue anchor, so opening a playlist that does not contain the
        # playing track re-anchors it on that playlist's first song.  This
        # field is only ever written in play_url(), which keeps the "now
        # playing" identity stable while the queue anchor moves around.
        self._playing_item = None
        self._shuffle_order = []
        self._shuffle_seed = None
        self._play_generation = 0
        # Sources manually queued to play after the current track
        self._next_sources = []
        # In-flight background stream downloads, kept referenced so the
        # QThreads are never garbage-collected while running
        self._resolve_workers = []
        # Direct network URL currently being streamed while its file
        # downloads in the background (None when playing a local source)
        self._streaming_source = None
        # Details of an in-progress swap from network stream to the
        # downloaded file (see _begin_local_switch)
        self._pending_switch = None
        # Pending verification that a finished swap really started playing
        # (the Qt backend can drop commands issued right after a source
        # change — see _verify_local_switch)
        self._local_switch_check = None
        # True once playback of an actual track has been initiated.  Before
        # that, _current_item is only a queue anchor and the UI must not
        # treat it as "now playing".
        self._has_media = False

        # start at a safe default volume and use a smooth curve for perception
        self.volume = 30
        self.audio.setVolume(self._to_audio_volume(self.volume))
        
        # Connect player signals
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self.signals.duration_changed.emit)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        
        # Auto-play next when media ends
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

    # ---------- core playback ----------
    def play_url(self, url):
        self._play_generation += 1
        generation = self._play_generation
        # A concrete track is being started — from now on the UI may treat
        # this as "now playing".
        self._has_media = True
        self._playing_item = url
        # New track: drop any in-flight swap and stream state from the last
        # one so they can never leak into this playback.
        self._streaming_source = None
        self._pending_switch = None
        self._local_switch_check = None

        # If the URL is already a local file path, play it directly.
        if isinstance(url, str) and os.path.exists(url):
            self.player.setSource(QUrl.fromLocalFile(url))
            self.player.play()
            return

        # Remote URL: resolving (a yt-dlp download) can take a long time, so
        # it must never run on the UI thread. Kick it off in the background;
        # the generation guard drops the result if the user has meanwhile
        # picked another track or switched views.  The worker first emits
        # the direct network URL (`streaming`) so playback starts right
        # away, then the downloaded file path (`resolved`).
        print(f"[player] resolving stream in background: {url}", flush=True)
        worker = StreamResolveWorker(url, generation)
        worker.streaming.connect(self._on_stream_available)
        worker.resolved.connect(self._on_stream_resolved)
        self._resolve_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._forget_resolve_worker(w))
        worker.start()

    def _on_stream_available(self, generation, url, stream_url):
        """Start playing the direct network stream immediately while the
        full file keeps downloading in the background."""
        if generation != self._play_generation:
            print("[player] stale stream probe dropped", flush=True)
            return
        print(f"[player] streaming from network while download finishes: {url}", flush=True)
        self._streaming_source = stream_url
        self.player.setSource(QUrl(stream_url))
        self.player.play()

    def _on_stream_resolved(self, generation, url, stream):
        """Start playback when a background resolve finishes (main thread)."""
        if generation != self._play_generation:
            print("[player] stale stream resolve dropped", flush=True)
            return
        if not stream:
            if self._streaming_source:
                # The download failed but the network stream is already
                # playing — keep streaming instead of cutting the audio.
                print(f"[player] download failed, continuing network stream: {url}", flush=True)
                return
            # A failed download must not be treated as a completed track.
            print(f"[player] stream resolve failed: {url}", flush=True)
            self.player.stop()
            return

        if self._streaming_source:
            # Already playing from the network — swap in the fully
            # downloaded file at the exact current position.
            self._streaming_source = None
            self._begin_local_switch(stream)
            return

        # Not streaming (cache hit or probe fallback): play the resolved source.
        if isinstance(stream, str) and not stream.startswith("http"):
            self.player.setSource(QUrl.fromLocalFile(stream))
        else:
            stream_url = QUrl(stream)
            self.player.setSource(stream_url)
        self.player.play()

    def _begin_local_switch(self, path):
        """Swap the playing network stream for the fully downloaded file.

        Captures the position and a wall-clock timestamp before switching.
        The resume itself waits for the swapped-in file to finish loading
        (see _resume_from_local_file_when_loaded) and then seeks to the
        captured position plus the time the switch took, so playback
        continues at the exact same spot.
        """
        position = self.player.position()
        was_playing = (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        self._pending_switch = {
            "position": position,
            "playing": was_playing,
            "captured_at": time.monotonic(),
            "generation": self._play_generation,
            "loading_seen": False,
        }
        print(f"[player] swapping to downloaded file at {position} ms", flush=True)
        self.player.setSource(QUrl.fromLocalFile(path))
        # Safety net: if the load events never arrive (they normally trigger
        # the resume — see _on_media_status_changed), force the attempt.
        QTimer.singleShot(
            2000, lambda: self._resume_from_local_file_when_loaded(force=True)
        )

    def _resume_from_local_file_when_loaded(self, force=False):
        """Resume the swapped-in downloaded file once it is truly loaded.

        The outgoing network stream keeps delivering stale mediaStatus
        events while the new file loads (a bogus LoadedMedia arrives even
        before the new source's own LoadingMedia), so "ready" is detected
        via the new source's Loading→Loaded sequence in
        _on_media_status_changed.  Seeking before the load completes makes
        the backend drop the seek and the track restarts from 0:00.
        """
        info = self._pending_switch
        if not info:
            return  # already resumed, or cancelled by stop()/next track
        if not force:
            status = self.player.mediaStatus()
            loaded = status in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
            if not loaded:
                return  # wait for the real LoadedMedia (or the 2 s timer)
        self._pending_switch = None
        self._resume_from_local_file(info)

    def _resume_from_local_file(self, info):
        """Seek the loaded file to the captured position and resume.

        Runs one event-loop turn after the file finished loading: issuing
        seek/play from inside the mediaStatusChanged handler re-enters the
        backend mid-transition and the commands get silently dropped.
        """
        if info["generation"] != self._play_generation:
            return
        target = info["position"]
        if info["playing"]:
            target += int((time.monotonic() - info["captured_at"]) * 1000)
        duration = self.player.duration()
        if duration > 0:
            target = min(target, max(0, duration - 1))
        target = max(0, target)
        self.player.setPosition(target)
        if info["playing"]:
            self.player.play()
            # Safety net: verify shortly that playback really started from
            # the right spot — if the backend dropped the commands anyway,
            # retry once.
            self._local_switch_check = {
                "generation": self._play_generation,
                "target": target,
            }
            QTimer.singleShot(500, self._verify_local_switch)
        print(f"[player] now playing the downloaded file from {target} ms", flush=True)

    def _verify_local_switch(self):
        """One-shot safety net for the stream→file swap.

        Catches the two ways the Qt backend can drop the swap commands:
        playback frozen at 0:00 (player claims Playing but the position
        never moves), and playback running from the wrong spot (the seek
        was ignored and the track restarted).  Anything else — the user
        pausing, seeking, stopping or changing tracks — is left alone.
        """
        check = self._local_switch_check
        self._local_switch_check = None
        if not check or check["generation"] != self._play_generation:
            return
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return  # user took over (pause/stop/next)
        position = self.player.position()
        status = self.player.mediaStatus()
        if position == 0 and status == QMediaPlayer.MediaStatus.LoadedMedia:
            # Frozen at 0:00 — the seek and the play were both dropped.
            print("[player] swap froze playback, retrying seek + play", flush=True)
            self.player.setPosition(check["target"])
            self.player.play()
        elif position < check["target"] - 1500:
            # Playing, but from the start instead of the captured position.
            print("[player] swap lost the seek, re-seeking", flush=True)
            self.player.setPosition(check["target"])

    def _on_position_changed(self, position):
        # While swapping the network stream for the downloaded file, the
        # position would briefly jump back to 0 and scare the seek bar —
        # hold the last real position until the swap completes.
        if self._pending_switch:
            return
        self.signals.position_changed.emit(position)

    def _on_playback_state_changed(self, state):
        # The stream→file swap passes through a brief Stopped state; hide it
        # so the UI does not flicker to "paused" mid-track.
        if self._pending_switch and state == QMediaPlayer.PlaybackState.StoppedState:
            return
        self.signals.state_changed.emit(state)

    def _forget_resolve_worker(self, worker):
        self._resolve_workers = [w for w in self._resolve_workers if w is not worker]

    def play(self, url=None):
        if url:
            self._current_item = url
            self.play_url(url)
            return

        if 0 <= self.index < len(self.queue):
            self._current_item = self.queue[self.index]
            self.play_url(self._current_item)

    # ---------- queue system ----------
    def set_queue(self, urls, current_item=None):
        new_base_queue = list(urls)
        self._base_queue = new_base_queue

        if current_item is not None:
            self._current_item = current_item
        elif self._current_item not in self._base_queue:
            self._current_item = self._base_queue[0] if self._base_queue else None

        # Clean up orphaned next-sources (keep queued-next duplicates of current track)
        self._next_sources = [s for s in self._next_sources if s in self._base_queue]

        if self.shuffle_enabled and self._base_queue:
            self._apply_shuffle(current_item=self._current_item, preserve_existing=True)
        else:
            self._shuffle_order = []
            self._ordered_queue = list(self._base_queue)
            self.queue = list(self._base_queue)
            if self._current_item in self._base_queue:
                self.index = self._base_queue.index(self._current_item)
            else:
                self.index = 0
            self._current_item = self.queue[self.index] if 0 <= self.index < len(self.queue) else None

        self._restore_next_sources()

    def _restore_next_sources(self):
        """Re-insert queued-next sources immediately after the current track.

        Copies stranded at an earlier anchor position (before the current
        index) are stripped as well, so re-anchoring after the current track
        moves (manual pick, shuffle change, ...) never leaves the same song
        queued twice or stuck behind the play position.
        """
        if not self._next_sources:
            return
        self._next_sources = [s for s in self._next_sources if s in self.queue]
        if not self._next_sources:
            return

        # Ensure index/current_item are valid before we slice the queue
        if self._current_item in self.queue:
            self.index = self.queue.index(self._current_item)
        elif self.queue:
            self.index = 0
            self._current_item = self.queue[0]
        else:
            self.index = -1
            self._current_item = None
            return

        # Keep the currently-playing track even when it is itself a queued
        # source; drop every other copy of the queued sources so each queue
        # request exists exactly once, right after the current track.
        current = self.queue[self.index]
        kept = [u for u in self.queue[:self.index] if u not in self._next_sources]
        kept.append(current)
        for url in self.queue[self.index + 1:]:
            if url not in self._next_sources and url != current:
                kept.append(url)
        self.queue = kept

        # The rebuild can move the current track forward (stale queued
        # copies were stripped in front of it), so re-sync the index to the
        # current track's new position before inserting after it.
        self.index = self.queue.index(current)

        insert_at = self.index + 1
        for src in self._next_sources:
            self.queue.insert(insert_at, src)
            insert_at += 1

    def queue_next(self, source):
        """Place source immediately after the current track."""
        if not source:
            return
        if source not in self._base_queue:
            self._base_queue.append(source)
        # A source from outside the current queue (e.g. a search result)
        # must join the live queue too, or _restore_next_sources would
        # drop it again right below.
        if source not in self.queue:
            self.queue.append(source)
        # Allow the same song to be queued multiple times
        self._next_sources.append(source)
        self._restore_next_sources()

    def next(self):
        self._advance()

    def previous(self):
        if self.index > 0:
            self.index -= 1
            self._current_item = self.queue[self.index]
            self.play()

    def play_at(self, idx):
        """Play the queue entry at idx (a manual pick from the UI).

        Queued-next sources are re-anchored right behind the newly picked
        track, so a song the user queued earlier still plays when this one
        ends.  Picking a song that was itself queued consumes one queue
        request, matching how _advance() consumes it on autoplay.
        """
        if not 0 <= idx < len(self.queue):
            return
        self.index = idx
        self._current_item = self.queue[idx]
        if self._current_item in self._next_sources:
            self._next_sources.remove(self._current_item)
        if self._next_sources:
            self._restore_next_sources()
        self.play()

    def _advance(self):
        """Advance to the next track and clean up played queued-next sources."""
        if not self.queue or self.index + 1 >= len(self.queue):
            return
        self.index += 1
        self._current_item = self.queue[self.index]
        if self._current_item in self._next_sources:
            self._next_sources.remove(self._current_item)
        self.play()

    def toggle_shuffle(self, enabled=None):
        if enabled is None:
            enabled = not self.shuffle_enabled

        self.shuffle_enabled = bool(enabled)
        if self.shuffle_enabled and self._base_queue:
            self._apply_shuffle(current_item=self._current_item)
            self._restore_next_sources()
        else:
            self._shuffle_order = []
            self._ordered_queue = list(self._base_queue)
            self.queue = list(self._base_queue)
            if self._current_item in self._base_queue:
                self.index = self._base_queue.index(self._current_item)
            else:
                self.index = 0
            self._current_item = self.queue[self.index] if 0 <= self.index < len(self.queue) else None
            self._restore_next_sources()

    def apply_synced_shuffle(self, enabled, seed, base_queue, current_item=None, next_sources=None):
        """Adopt shuffle state broadcast by a synced-up peer.

        The peer sends its exact seed plus the base queue it shuffled, so
        both machines reconstruct the exact same randomized order and
        continue through the same upcoming tracks. Sources arrive already
        mapped to this machine (local file paths or stream URLs).
        """
        self._base_queue = [u for u in (base_queue or []) if u]
        self._next_sources = [
            s for s in (next_sources or []) if s and s in self._base_queue
        ]
        try:
            self._shuffle_seed = int(seed)
        except (TypeError, ValueError):
            self._shuffle_seed = None
        self.shuffle_enabled = bool(enabled)

        if not self._base_queue:
            self.queue = []
            self.index = -1
            self._current_item = None
            return

        if self.shuffle_enabled:
            self._apply_shuffle(
                current_item=current_item or self._current_item,
                keep_seed=True,
            )
            self._restore_next_sources()
        else:
            self._shuffle_order = []
            self._ordered_queue = list(self._base_queue)
            self.queue = list(self._base_queue)
            if current_item in self.queue:
                self.index = self.queue.index(current_item)
            else:
                self.index = 0
            self._current_item = self.queue[self.index] if self.queue else None
            self._restore_next_sources()

    def adopt_synced_queue(self, sources, index, next_sources=None):
        """Adopt a synced-up peer's exact play order (sources already local
        to this machine), so Next / autoplay follow the same tracks on both
        machines even when their queues had drifted apart."""
        self.queue = [s for s in (sources or []) if s]
        if not self.queue:
            return
        try:
            self.index = int(index)
        except (TypeError, ValueError):
            self.index = 0
        if not 0 <= self.index < len(self.queue):
            self.index = 0
        self._current_item = self.queue[self.index]

        # First-seen order of the adopted queue keeps the base queue sane
        # for later local shuffle / sort changes.
        base = []
        for source in self.queue:
            if source not in base:
                base.append(source)
        self._base_queue = base
        self._next_sources = [s for s in (next_sources or []) if s in base]
        if self.shuffle_enabled:
            self._shuffle_order = list(self.queue)
        else:
            self._ordered_queue = list(self.queue)
        self._restore_next_sources()

    def _apply_shuffle(self, current_item=None, preserve_existing=False, keep_seed=False):
        if not self._base_queue:
            self.queue = []
            self.index = -1
            self._current_item = None
            return

        if current_item in self._base_queue:
            self._current_item = current_item
        elif self._base_queue:
            self._current_item = self._base_queue[0]
        else:
            self._current_item = None

        if preserve_existing and self._shuffle_order:
            base_set = set(self._base_queue)
            shuffle_set = set(self._shuffle_order)
            if base_set == shuffle_set:
                self.queue = list(self._shuffle_order)
                # Move the chosen song to the front so that after it finishes
                # playback continues through the rest of the shuffle order.
                if self._current_item in self.queue:
                    self.queue.remove(self._current_item)
                    self.queue.insert(0, self._current_item)
                self.index = 0
                self._ordered_queue = list(self.queue)
                return

        # Need a new shuffle — use a stored seed so the same base
        # queue stays in the same randomized order across set_queue calls.
        # keep_seed reuses the stored seed as-is (synced machines apply the
        # peer's seed this way so both reconstruct the identical order).
        if keep_seed:
            if self._shuffle_seed is None:
                self._shuffle_seed = random.randint(0, 2 ** 31 - 1)
        elif not preserve_existing or self._shuffle_seed is None:
            self._shuffle_seed = random.randint(0, 2 ** 31 - 1)

        rng = random.Random(self._shuffle_seed)
        others = [u for u in self._base_queue if u != self._current_item]
        rng.shuffle(others)

        if self._current_item is not None:
            shuffled = [self._current_item] + others
        else:
            shuffled = others

        self._shuffle_order = list(shuffled)
        self._ordered_queue = list(shuffled)
        self.queue = list(shuffled)
        self.index = 0
        self._current_item = self.queue[0] if self.queue else None

    # ---------- controls ----------
    def pause(self):
        # User intent overrides any pending swap-verification retry.
        self._local_switch_check = None
        if self._pending_switch:
            # A swap is mid-flight: let it finish loading the downloaded
            # file, but the user wants it paused, not auto-resumed.
            self._pending_switch["playing"] = False
        self.player.pause()

    def resume(self):
        self.player.play()

    def stop(self):
        # An in-flight stream→file swap must never fire after an explicit stop.
        self._pending_switch = None
        self._local_switch_check = None
        self.player.stop()

    def shutdown(self):
        """Wait briefly for in-flight background resolves so their QThreads
        are not destroyed while running at application exit."""
        for worker in list(self._resolve_workers):
            worker.wait(2000)
        self._resolve_workers = []
    
    def seek(self, position):
        """Seek to position (0.0-1.0 relative to duration)"""
        duration = self.player.duration()
        if duration > 0:
            ms = int(position * duration)
            self.player.setPosition(ms)
    
    def get_position(self):
        """Get current position in milliseconds"""
        return self.player.position()
    
    def get_duration(self):
        """Get total duration in milliseconds"""
        return self.player.duration()

    def set_volume(self, value):
        self.volume = value
        self.audio.setVolume(self._to_audio_volume(value))

    def _to_audio_volume(self, slider_value):
        normalized = max(0.0, min(slider_value / 100.0, 1.0))
        max_gain = 0.50
        return ((10 ** normalized - 1) / 9) * max_gain

    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())

    def _on_audio_outputs_changed(self):
        """Switch to the new default audio output device when system default changes."""
        new_device = self._media_devices.defaultAudioOutput()
        self.audio.setDevice(new_device)
        print(f"[AUDIO] Switched to default output: {new_device.description()}", flush=True)
    
    def _on_media_status_changed(self, status):
        """Handle media status changes - swap to the downloaded file once it
        is really loaded, and auto-play next when the current track ends"""
        if self._pending_switch:
            # A stream→file swap is in flight.  The outgoing network stream
            # still emits stale status events (including a bogus LoadedMedia
            # that arrives BEFORE the new file's own LoadingMedia), so "the
            # downloaded file is ready" is only trusted on the new source's
            # LoadingMedia → LoadedMedia sequence.
            if status == QMediaPlayer.MediaStatus.LoadingMedia:
                self._pending_switch["loading_seen"] = True
            elif (
                status == QMediaPlayer.MediaStatus.LoadedMedia
                and self._pending_switch.get("loading_seen")
            ):
                # Loaded for real — resume one event-loop turn later: never
                # issue seek/play from inside this handler, the backend is
                # mid-transition and would drop them.
                QTimer.singleShot(0, self._resume_from_local_file_when_loaded)
                return
            elif status == QMediaPlayer.MediaStatus.InvalidMedia:
                # The file failed to load mid-swap: stop waiting and just
                # play what we have rather than freezing half-swapped.
                print("[player] downloaded file failed to load, resuming from 0", flush=True)
                self._pending_switch = None
                self.player.play()
                return

        # Auto-play next when the media has ended
        if status != QMediaPlayer.MediaStatus.EndOfMedia or not self.autoplay_enabled:
            return

        generation = self._play_generation
        QTimer.singleShot(0, lambda g=generation: self._emit_autoplay_next(g))

    def _emit_autoplay_next(self, generation):
        """Defer the queue transition until Qt finishes its current media event.
        
        The generation argument lets us silently ignore callbacks that were
        issued for an earlier play_url() call.
        """
        if generation == self._play_generation and self.autoplay_enabled:
            self.signals.autoplay_next.emit()