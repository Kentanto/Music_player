from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
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


class Player:
    def __init__(self):
        self.player = QMediaPlayer()
        self.audio = QAudioOutput(QMediaDevices.defaultAudioOutput())
        self.signals = PlayerSignals()

        self.player.setAudioOutput(self.audio)

        self.queue = []
        self.index = -1
        self.autoplay_enabled = True
        self.shuffle_enabled = False
        self._base_queue = []
        self._ordered_queue = []
        self._current_item = None
        self._shuffle_order = []
        self._priority_queue = []
        self._end_signal_pending = False

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
        self._end_signal_pending = False
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
        queue_changed = self._base_queue != new_base_queue
        self._base_queue = new_base_queue

        if current_item is not None:
            self._current_item = current_item
        elif self._current_item not in self._base_queue:
            self._current_item = self._base_queue[0] if self._base_queue else None

        if self.shuffle_enabled and self._base_queue:
            if queue_changed:
                old_order = [url for url in self._shuffle_order if url in self._base_queue]
                if old_order:
                    merged_order = list(old_order)
                    for position, url in enumerate(self._base_queue):
                        if url in old_order:
                            continue
                        old_before = sum(
                            1 for previous in self._base_queue[:position] if previous in old_order
                        )
                        new_before = sum(
                            1 for previous in self._base_queue[:position] if previous not in old_order
                        )
                        merged_order.insert(old_before + new_before, url)
                    self._shuffle_order = merged_order
                else:
                    self._apply_shuffle(current_item=self._current_item)
            elif not self._shuffle_order:
                self._apply_shuffle(current_item=self._current_item)

            self._ordered_queue = list(self._shuffle_order)
            self.queue = list(self._shuffle_order)
            if self._current_item in self.queue:
                self.index = self.queue.index(self._current_item)
            elif self.queue:
                self.index = 0
            else:
                self.index = -1
            self._current_item = self.queue[self.index] if 0 <= self.index < len(self.queue) else None
        else:
            self.queue = list(self._base_queue)
            self._ordered_queue = list(self._base_queue)
            if self._current_item in self._base_queue:
                self.index = self._base_queue.index(self._current_item)
            else:
                self.index = 0
            self._current_item = self.queue[self.index] if 0 <= self.index < len(self.queue) else None

        self._priority_queue = [url for url in self._priority_queue if url in self.queue and url != self._current_item]
        self._insert_priority_queue()

    def _insert_priority_queue(self):
        if not self._priority_queue or self._current_item not in self.queue:
            return

        self.queue = [url for url in self.queue if url not in self._priority_queue]
        self.index = self.queue.index(self._current_item)
        insert_at = self.index + 1
        self.queue[insert_at:insert_at] = self._priority_queue

    def queue_next(self, source):
        """Place a track in the FIFO priority queue after the current track."""
        if not source or source == self._current_item:
            return

        self._priority_queue = [url for url in self._priority_queue if url != source]
        self._priority_queue.append(source)
        if source not in self.queue:
            self.queue.append(source)
        self._insert_priority_queue()

    def next(self):
        if self._current_item in self._priority_queue:
            self._priority_queue.remove(self._current_item)
        if self.index + 1 < len(self.queue):
            self.index += 1
            self.play()

    def previous(self):
        if self.index - 1 >= 0:
            self.index -= 1
            self.play()

    def toggle_shuffle(self, enabled=None):
        if enabled is None:
            enabled = not self.shuffle_enabled

        self.shuffle_enabled = bool(enabled)
        if self.shuffle_enabled and self._base_queue:
            if not self._shuffle_order:
                self._apply_shuffle(current_item=self._current_item)
            else:
                self.queue = list(self._shuffle_order)
                self.index = self.queue.index(self._current_item) if self._current_item in self.queue else 0
                self._current_item = self.queue[self.index] if self.queue else None
        else:
            self._shuffle_order = []
            self._priority_queue = []
            self._ordered_queue = list(self._base_queue)
            self.queue = list(self._base_queue)
            self.index = (
            self._base_queue.index(self._current_item)
            if self._current_item in self._base_queue
            else 0)

            self._current_item = self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def _apply_shuffle(self, current_item=None):
        if not self._base_queue:
            self.queue = []
            self.index = -1
            self._current_item = None
            return
        
        new_base_queue = list(self._base_queue)
        if current_item in new_base_queue:
            self._current_item = current_item
        elif new_base_queue:
            self._current_item = new_base_queue[0]
        else:
            self._current_item = None

        shuffled = random.sample(self._base_queue, len(self._base_queue))
        self._ordered_queue = list(shuffled)
        self.queue = list(self._ordered_queue)
        self._shuffle_order = list(self.queue)

        if current_item is not None and current_item in self.queue:
            self.index = self.queue.index(current_item)
        elif self.queue:
            self.index = 0
        else:
            self.index = -1

        self._current_item = self.queue[self.index] if 0 <= self.index < len(self.queue) else None

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
        max_gain = 0.20
        return ((10 ** normalized - 1) / 9) * max_gain
    
    def _on_media_status_changed(self, status):
        """Handle media status changes - auto-play next when current ends"""
        # Auto-play next when the media has ended
        if status != QMediaPlayer.MediaStatus.EndOfMedia or not self.autoplay_enabled:
            return
        if self._end_signal_pending:
            return

        self._end_signal_pending = True
        QTimer.singleShot(0, self._emit_autoplay_next)

    def _emit_autoplay_next(self):
        """Defer the queue transition until Qt finishes its current media event."""
        if self.autoplay_enabled:
            self.signals.autoplay_next.emit()