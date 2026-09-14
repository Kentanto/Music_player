from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
try:
    from PySide6.QtMultimedia import QAudioBufferOutput
except ImportError:
    QAudioBufferOutput = None
from PySide6.QtCore import QUrl, QTimer, Signal, QObject
import os
import random

from cache import resolve_stream


class PlayerSignals(QObject):
    """Signals for player events"""
    position_changed = Signal(int)  # Current position in ms
    duration_changed = Signal(int)  # Duration in ms
    state_changed = Signal(object)  # QMediaPlayer state enum
    track_ended = Signal()  # Emitted when track finishes naturally
    autoplay_next = Signal()  # Signal to controller to play next
    audio_buffer_received = Signal(object)


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
        self._shuffle_order = []
        self._shuffle_seed = None
        self._play_generation = 0
        # Sources manually queued to play after the current track
        self._next_sources = []

        # start at a safe default volume and use a smooth curve for perception
        self.volume = 30
        self.audio.setVolume(self._to_audio_volume(self.volume))
        
        # Connect player signals
        self.player.positionChanged.connect(self.signals.position_changed.emit)
        self.player.durationChanged.connect(self.signals.duration_changed.emit)
        self.player.playbackStateChanged.connect(self.signals.state_changed.emit)
        
        # Auto-play next when media ends
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

    # ---------- core playback ----------
    def play_url(self, url):
        self._play_generation += 1
        # If the URL is already a local file path, play it directly.
        if isinstance(url, str) and os.path.exists(url):
            self.player.setSource(QUrl.fromLocalFile(url))
            self.player.play()
            return

        stream = resolve_stream(url)
        if not stream:
            # A failed download must not be treated as a completed track.
            self.player.stop()
            return

        # Convert local file path to file:// URL
        if isinstance(stream, str) and not stream.startswith("http"):
            self.player.setSource(QUrl.fromLocalFile(stream))
        else:
            stream_url = QUrl(stream)
            self.player.setSource(stream_url)
        self.player.play()

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
        """Re-insert queued-next sources immediately after the current track."""
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

        # Only strip duplicates from AFTER the current position so the
        # currently-playing track is never removed.
        insert_at = self.index + 1
        tail = [u for u in self.queue[insert_at:] if u not in self._next_sources]
        self.queue = self.queue[:insert_at] + tail

        for src in self._next_sources:
            self.queue.insert(insert_at, src)
            insert_at += 1

    def queue_next(self, source):
        """Place source immediately after the current track."""
        if not source:
            return
        if source not in self._base_queue:
            self._base_queue.append(source)
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

    def _apply_shuffle(self, current_item=None, preserve_existing=False):
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
        if not preserve_existing or self._shuffle_seed is None:
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
        self.player.pause()

    def resume(self):
        self.player.play()

    def stop(self):
        self.player.stop()
    
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

    def _on_audio_outputs_changed(self):
        """Switch to the new default audio output device when system default changes."""
        new_device = self._media_devices.defaultAudioOutput()
        self.audio.setDevice(new_device)
        print(f"[AUDIO] Switched to default output: {new_device.description()}", flush=True)
    
    def _on_media_status_changed(self, status):
        """Handle media status changes - auto-play next when current ends"""
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