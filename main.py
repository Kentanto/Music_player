import contextlib
import sys
import time
import warnings
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton, QSlider,
    QTextEdit, QVBoxLayout,
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QThread, QTimer, Signal

from ui import MainWindow
from ui.queue_panel import ThumbnailLoader
from player import Player
from media_hotkeys import install_media_hotkeys
from search import SearchWorker
from db import (
    init_db,
    create_playlist,
    get_playlists,
    add_song_to_playlist,
    get_playlist_songs,
    remove_playlist_song,
    remove_song_everywhere,
    rename_playlist,
    rename_playlist_song,
    rename_song_globally,
    delete_playlist,
    get_app_setting,
    set_app_setting,
    thumbnail_path_for_audio,
    get_track_metadata,
    get_all_playlist_songs_flat,
    get_playlist_by_id,
    get_spotify_links,
    get_ytdlp_cookie_options,
    set_ytdlp_cookie_config,
    to_portable_path,
    resolve_stored_path,
)
from metadata_fetcher import MetadataFetcher
from spotify_download import SpotifyImportWorker, SpotifyLinkWorker, SpotifySyncWorker
from retry_failed import RetryFailedWorker, parse_failures
from cec_remote import CecRemoteListener
from mocute_listener import MocuteListener
from net_sync import SyncEngine, normalize_mac, local_mac, DEFAULT_SYNC_PORT

# Suppress all warnings
warnings.filterwarnings("ignore")

# How long a machine waits at the end of a track for its synced peer to
# finish the same track before moving on anyway (peer stalled or gone).
PEER_FINISH_TIMEOUT_MS = 15000


class AddSongWorker(QThread):
    """Downloads a song into a playlist off the UI thread so the window
    never freezes during a yt-dlp download (same pattern as SearchWorker)."""

    add_finished = Signal(str)  # saved file path
    add_failed = Signal(str)    # reason

    def __init__(self, title, url, playlist_id, artist=None, thumbnail=None, parent=None):
        super().__init__(parent)
        self._title = title
        self._url = url
        self._playlist_id = playlist_id
        self._artist = artist
        self._thumbnail = thumbnail

    def run(self):
        try:
            saved_path = add_song_to_playlist(
                self._title,
                self._url,
                self._playlist_id,
                artist=self._artist,
                thumbnail=self._thumbnail,
            )
        except Exception as error:
            print(f"[playlist-add] worker exception: {type(error).__name__}: {error}", flush=True)
            self.add_failed.emit(str(error))
            return
        if saved_path:
            self.add_finished.emit(saved_path)
        else:
            self.add_failed.emit("add_song_to_playlist returned no path")


class MusicAppController:
    """Business logic controller - bridges UI and backend"""
    
    def __init__(self, window):
        self.window = window
        self.player = Player()
        self.current_results = []
        self.metadata_fetcher = None  # Background thread for duration checking
        self.search_worker = None  # Background thread for YouTube searches
        self._search_generation = 0  # Drops results from superseded searches
        self._retired_fetchers = []  # Retired fetchers kept alive until they exit
        self._retired_search_workers = []  # Retired search workers, same
        self._cover_token = 0  # Drops superseded cover-art loads
        self.spotify_import_worker = None
        self.spotify_link_worker = None
        self.spotify_sync_worker = None
        self.add_song_worker = None
        self.retry_failed_worker = None
        self.cec_remote = None
        self.gamepad = None
        self.mpris = None  # Linux MPRIS bridge (installed in install_mpris)
        self.active_queue_urls = []
        self.current_playlist_id = None
        # (source, track dict) of the last track whose info was shown as
        # "now playing" — lets the title/artwork survive switching to a view
        # that does not contain the playing song.
        self._now_playing_item = None

        # ---------- network sync (multiroom playback) ----------
        self.sync = None                    # SyncEngine while enabled
        self._sync_own_id = None
        self._sync_leader_id = None         # machine id of the last command sender
        self._sync_leader_state = None      # last "state" message from the leader
        self._sync_leader_received_at = 0.0  # monotonic clock of the above
        self._sync_last_local_command = 0.0  # monotonic time of our last broadcast
        self._sync_suppress_depth = 0      # > 0 while applying a remote command
        self._sync_peer_status = {}         # mac -> last known status
        self._sync_peer_states = {}         # mac -> (state dict, received_at)
        self._sync_peer_finished = {}       # mac -> finished-track record from a peer
        self._sync_pending_advance = None   # end-of-track transition held for a peer
        self._sync_last_join_attempt = 0.0  # rate limit for joining a playing peer
        self._sync_dialog_refresh = None    # status refresh callback while open
        # source -> {file, url} identity pairs for sync broadcasts (the URL
        # for a file never changes, so one lookup per song is enough)
        self._sync_identity_cache = {}
        self._sync_state_timer = QTimer(self.window)
        self._sync_state_timer.setInterval(1000)
        self._sync_state_timer.timeout.connect(self._sync_send_state)
        self._sync_pending_timer = QTimer(self.window)
        self._sync_pending_timer.setSingleShot(True)
        self._sync_pending_timer.setInterval(PEER_FINISH_TIMEOUT_MS)
        self._sync_pending_timer.timeout.connect(self._sync_pending_advance_timeout)
        self._start_sync_engine()
        
        # Connect player signals to update UI
        self.player.signals.position_changed.connect(self.on_player_position_changed)
        self.player.signals.duration_changed.connect(self.on_player_duration_changed)
        self.player.signals.state_changed.connect(self.on_player_state_changed)
        self.player.signals.autoplay_next.connect(self.handle_next_autoplay)
        self.player.signals.audio_buffer_received.connect(self.window.eq_visualizer.update_buffer)
        self.player.signals.audio_buffer_received.connect(
            self.window.fullscreen_player.eq_visualizer.update_buffer
        )
        
        # Connect all UI signals to handlers
        self.connect_handlers()

        saved_shuffle = get_app_setting("shuffle_enabled", "False")
        self.player.shuffle_enabled = saved_shuffle.lower() == "true"
        self._publish_shuffle_state(self.player.shuffle_enabled)

        # Restore touch-panel mode from DB
        saved_touch = get_app_setting("touch_mode", "False")
        if saved_touch.lower() == "true":
            self.window.apply_touch_mode(True)
        self.window.touch_mode_changed.connect(
            lambda enabled: set_app_setting("touch_mode", str(bool(enabled)))
        )

        # Restore the queue row size chosen in the settings dialog
        self._apply_queue_scale()
        
        # Restore the last playlist or show playlists first
        self._restore_last_view()
        
        # Set initial volume AFTER handlers are connected, restoring the
        # volume saved in the database (falls back to 30 on first run).
        saved_volume = get_app_setting("volume", 30)
        try:
            saved_volume = int(float(saved_volume))
        except (TypeError, ValueError):
            saved_volume = 30
        self.window.player_bar.volume_slider.setValue(max(0, min(saved_volume, 100)))
    
    def on_player_position_changed(self, position_ms):
        """Update seek bar as song plays"""
        if self.mpris:
            self.mpris.set_position_ms(position_ms)
        duration_ms = self.player.get_duration()
        if duration_ms > 0:
            position_fraction = position_ms / duration_ms
            self.window.update_player_time(position_ms / 1000, duration_ms / 1000)
            self.window.set_seek_position(position_fraction)
            self.window.fullscreen_player.set_time(position_ms / 1000, duration_ms / 1000)
            self.window.fullscreen_player.set_seek_position(position_fraction)
    
    def on_player_duration_changed(self, duration_ms):
        """Update total duration display"""
        if self.mpris:
            self.mpris.set_track_length(duration_ms)
        self.window.update_player_time(0, duration_ms / 1000)
        self.window.fullscreen_player.set_time(0, duration_ms / 1000)
    
    def on_player_state_changed(self, state):
        """Update UI button state when playback changes."""
        is_playing = state == self.player.player.PlaybackState.PlayingState
        self.window.set_play_pause_state(is_playing)
        self.window.fullscreen_player.set_play_pause_state(is_playing)
        if self.mpris:
            if state == self.player.player.PlaybackState.PlayingState:
                self.mpris.set_playback_status("Playing")
            elif state == self.player.player.PlaybackState.PausedState:
                self.mpris.set_playback_status("Paused")
            else:
                self.mpris.set_playback_status("Stopped")
            self._push_mpris_capabilities()
    
    def connect_handlers(self):
        """Connect UI signals to business logic"""
        self.window.search_requested.connect(self.handle_search)
        self.window.play_pause_track.connect(self.handle_play_pause)
        self.window.play_track_index.connect(self.handle_play_item)
        self.window.queue_next_requested.connect(self.handle_queue_next)
        self.window.queue_panel.remove_requested.connect(self.handle_remove_song)
        self.window.queue_panel.rename_requested.connect(self.handle_rename_item)
        self.window.queue_panel.delete_playlist_requested.connect(self.handle_delete_playlist)
        self.window.queue_panel.playback_order_changed.connect(self.handle_playback_order_changed)
        self.window.add_to_playlist_requested.connect(self.handle_add_to_playlist)
        self.window.track_selected.connect(self.handle_track_selected)
        self.window.next_track.connect(self.handle_next)
        self.window.prev_track.connect(self.handle_prev)
        self.window.shuffle_toggled.connect(self.handle_shuffle_toggle)
        self.window.load_playlists.connect(self.handle_load_playlists)
        self.window.add_to_playlist.connect(self.handle_add_to_playlist)
        self.window.import_list_requested.connect(self.handle_import_list)
        self.window.link_spotify_requested.connect(self.handle_link_spotify)
        self.window.view_failures_requested.connect(self.handle_view_failures)
        self.window.open_playlist_requested.connect(self.handle_open_playlist)
        self.window.volume_changed.connect(self.handle_volume_change)
        self.window.seek_requested.connect(self.handle_seek)
        self.window.seek_delta_requested.connect(self.handle_seek_delta)
        self.window.fullscreen_requested.connect(self.handle_fullscreen)
        self.window.stop_requested.connect(self.handle_stop)
        self.window.volume_mute.connect(self.handle_volume_mute)
        self.window.network_sync_requested.connect(self.handle_network_sync)
        self.window.settings_requested.connect(self.handle_settings)

        fullscreen = self.window.fullscreen_player
        fullscreen.play_pause_clicked.connect(self.handle_play_pause)
        fullscreen.next_clicked.connect(self.handle_next)
        fullscreen.prev_clicked.connect(self.handle_prev)
        fullscreen.shuffle_toggled.connect(self.handle_shuffle_toggle)
        fullscreen.volume_changed.connect(self.handle_volume_change)

        self.cec_remote = CecRemoteListener(self.window)
        self.cec_remote.play_pause.connect(self.handle_play_pause)
        self.cec_remote.next_track.connect(self.handle_next)
        self.cec_remote.previous_track.connect(self.handle_prev)
        self.cec_remote.stop_requested.connect(self.handle_stop)
        self.cec_remote.back_requested.connect(self.handle_back)
        self.cec_remote.navigation.connect(self.window.navigate_remote)
        self.cec_remote.select_requested.connect(self.window.activate_remote_target)
        if self.cec_remote.available():
            self.cec_remote.start()

        self.gamepad = MocuteListener(self.window)
        self.gamepad.play_pause.connect(self.handle_play_pause)
        self.gamepad.next_track.connect(self.handle_next)
        self.gamepad.previous_track.connect(self.handle_prev)
        self.gamepad.stop_requested.connect(self.handle_stop)
        self.gamepad.back_requested.connect(self.handle_back)
        self.gamepad.navigation.connect(self.window.navigate_remote)
        self.gamepad.select_requested.connect(self.window.activate_remote_target)
        self.gamepad.volume_up.connect(self.handle_volume_up)
        self.gamepad.volume_down.connect(self.handle_volume_down)
        self.gamepad.fullscreen_requested.connect(self.handle_fullscreen)
        if self.gamepad.available():
            self.gamepad.start()
    
    def handle_search(self, query):
        """Search YouTube for songs (off the UI thread)."""
        print(f"Searching: {query}")
        self._search_generation += 1
        generation = self._search_generation

        # Retire an in-flight search without blocking the UI thread; its
        # results get dropped by the generation check when they arrive.
        self._retired_search_workers = [w for w in self._retired_search_workers if w.isRunning()]
        if self.search_worker and self.search_worker.isRunning():
            self._retired_search_workers.append(self.search_worker)

        # Busy cursor so the user sees the search is running (app stays live).
        QApplication.restoreOverrideCursor()
        QApplication.setOverrideCursor(Qt.WaitCursor)

        self.search_worker = SearchWorker(query)
        self.search_worker.search_finished.connect(
            lambda results, g=generation: self._on_search_results(g, results)
        )
        self.search_worker.start()

    def _on_search_results(self, generation, results):
        """Show search results unless a newer search or view switch happened."""
        QApplication.restoreOverrideCursor()
        if generation != self._search_generation:
            print("[search] stale search results dropped", flush=True)
            return
        self.current_results = results or []

        # Start background metadata fetcher to remove long videos
        if self.current_results:
            self._start_metadata_fetcher()
        self._sync_queue_from_current_items()

    def handle_play(self):
        """Play current track selection or resume if paused"""

        if self.player.get_duration() > 0:
            self.player.resume()
            self._update_now_playing()
            self._sync_send("resume", {"position_ms": self.player.get_position()})
            return

        item = self.window.queue_panel.get_current_item()
        if not item:
            return

        self.handle_play_item(item)
    
    def handle_play_item(self, item):
        """Play a track from a dict item (shuffle-safe, index-free).

        Local picks are broadcast to synced-up peers; tracks a peer picked
        arrive through _sync_apply_play, which calls _play_item directly so
        they are never re-broadcast back.
        """
        played = self._play_item(item)
        if played:
            payload = self._sync_play_payload(self.player.get_position())
            if payload:
                self._sync_send("play", payload)

    def _play_item(self, item):
        """Play a track and return its source, or None when nothing played."""
        self._sync_cancel_pending_advance()
        if not item or item.get("type") == "playlist":
            return None

        selected_url = self._resolve_url(item)
        if not selected_url:
            return None

        # If the song is already in the active player queue, just play it
        # WITHOUT rebuilding/touching shuffle order.  play_at() re-anchors
        # any queued-next song right behind this pick, so a song the user
        # queued earlier still plays when this one ends.
        if selected_url in self.player.queue:
            self.player.play_at(self.player.queue.index(selected_url))
            self.active_queue_urls = list(self.player.queue)
            self._refresh_queue_display()
            self._update_now_playing()
            return selected_url

        urls = self.active_queue_urls or self.player._base_queue or self.window.get_current_queue_urls()
        if not urls:
            urls = [selected_url]
        elif selected_url not in urls:
            urls = [selected_url] + [u for u in urls if u != selected_url]

        self.active_queue_urls = list(urls)
        self.player.set_queue(urls, current_item=selected_url)
        self.player.play()

        self._refresh_queue_display()
        self._update_now_playing()
        return selected_url

    def handle_track_selected(self, item):
        """Selection only — never rewrite "now playing".

        The thumbnail, title, and artist must only change when a track
        actually starts playing. So instead of previewing the selected
        row, re-assert the current now-playing display: whatever is
        really playing stays on the cover pane, player bar, fullscreen
        view and MPRIS (and "No track selected" while nothing has ever
        played).
        """
        self._update_now_playing()
    
    def handle_pause(self):
        self.player.pause()
        self._sync_send("pause", {"position_ms": self.player.get_position()})
    
    def handle_resume(self):
        if self.player.get_duration() <= 0 and self._start_queue_if_idle():
            # Nothing loaded yet (fresh open): start the queue instead of
            # resuming an empty player.
            return
        self.player.resume()
        self._sync_send("resume", {"position_ms": self.player.get_position()})
    
    def handle_play_pause(self):
        state = self.player.player.playbackState()

        if state == self.player.player.PlaybackState.PlayingState:
            self.player.pause()
            self._sync_send("pause", {"position_ms": self.player.get_position()})
            return

        if self.player.get_duration() > 0:
            self.player.resume()
            self._update_now_playing()
            self._sync_send("resume", {"position_ms": self.player.get_position()})
            return

        item = self.window.queue_panel.get_current_item()
        if item:
            self.handle_play_item(item)
            return

        # No selection (nothing is auto-selected on open anymore): start
        # from the front of the active queue so Play still works.
        self._start_queue_if_idle()

    def _start_queue_if_idle(self):
        """When nothing is loaded, start playing the front of the queue."""
        if not self.player.queue:
            return False
        self.handle_play_item({"type": "track", "url": self.player.queue[0]})
        return True

    def handle_next(self):
        self._sync_cancel_pending_advance()
        before = self._playing_source()
        self.player.next()
        self._refresh_queue_display()
        self._update_now_playing()
        self._sync_after_track_change(before)
    
    def handle_queue_next(self, item):
        """Insert the selected item as the next track in the current queue.

        Broadcast to synced-up peers so the song lands in every machine's
        queue, keeping "what plays next" identical everywhere.
        """
        if not item or item.get("type") == "playlist":
            return

        source = self._resolve_url(item)
        if not source:
            return

        self.player.queue_next(source)
        self.active_queue_urls = list(self.player.queue)
        self._refresh_queue_display()

        payload = self._sync_source_identity(source)
        payload["title"] = item.get("title")
        payload["artist"] = item.get("artist")
        self._sync_send("queue_next", payload)
    
    def handle_next_autoplay(self):
        """Auto-play the next track when the current one finishes.

        Synced machines do not chase each other's position while a song
        plays — drift is fine. The one coordination point is the end of a
        track: the machine that finishes first announces it and waits
        (up to a timeout) for the peer to finish the same track too, so
        both units play every song to the end before moving on.
        """
        source = self._playing_source()
        if not source:
            return
        identity = self._sync_identity(source)
        if self.sync and self._sync_peer_playing_same(identity):
            self._sync_send("finished", self._sync_track_info(self.player.get_position()))
            if not self._sync_peer_finished_same(identity):
                self._sync_wait_for_peer_finish(identity)
                return
        self._advance_after_track_end()

    def _advance_after_track_end(self):
        before = self._playing_source()
        self.player._advance()
        self._refresh_queue_display()
        self._update_now_playing()
        self._sync_after_track_change(before)
    
    def handle_prev(self):
        self._sync_cancel_pending_advance()
        before = self._playing_source()
        self.player.previous()
        self._refresh_queue_display()
        self._update_now_playing()
        self._sync_after_track_change(before)

    # ---------- MPRIS (Linux media keys / media widgets) ----------
    def install_mpris(self):
        """Register the app as an MPRIS media player so the desktop's
        media keys control playback even when the window is unfocused."""
        if sys.platform == "win32":
            return None
        try:
            from mpris_service import MprisService
            self.mpris = MprisService()
        except Exception as error:
            print(f"[MPRIS] unavailable: {error}", flush=True)
            self.mpris = None
            return None

        m = self.mpris
        m.play_pause_requested.connect(self.handle_play_pause)
        m.play_requested.connect(self.handle_resume)
        m.pause_requested.connect(self.handle_pause)
        m.next_requested.connect(self.handle_next)
        m.previous_requested.connect(self.handle_prev)
        m.stop_requested.connect(self.handle_mpris_stop)
        m.seek_requested.connect(self.handle_mpris_seek)
        m.set_position_requested.connect(self.handle_mpris_set_position)
        m.shuffle_requested.connect(self.handle_shuffle_toggle)
        m.volume_requested.connect(lambda v: self.handle_volume_change(int(round(v * 100))))
        m.raise_requested.connect(self.handle_mpris_raise)
        m.quit_requested.connect(self.handle_mpris_quit)

        # Publish the current player state so widgets start in sync.
        self.mpris.set_volume(max(0, min(self.player.volume, 100)) / 100.0)
        self.mpris.set_shuffle(self.player.shuffle_enabled)
        self._push_mpris_capabilities()
        return self.mpris

    def _push_mpris_capabilities(self):
        if not self.mpris:
            return
        queue = self.player.queue or []
        index = self.player.index
        state = self.player.player.playbackState()
        playing = state == self.player.player.PlaybackState.PlayingState
        paused = state == self.player.player.PlaybackState.PausedState
        self.mpris.set_capabilities(
            can_play=bool(self.player._current_item),
            can_pause=playing or paused,
            can_go_next=0 <= index + 1 < len(queue),
            can_go_previous=index > 0,
            can_seek=self.player.get_duration() > 0,
        )

    def handle_mpris_stop(self):
        self.handle_stop()

    def handle_mpris_seek(self, delta_ms):
        duration = self.player.get_duration()
        if duration <= 0:
            return
        target = max(0, min(self.player.get_position() + int(delta_ms), duration - 1))
        self.player.player.setPosition(target)
        if self.mpris:
            # Keep the polled Position accurate even while paused.
            self.mpris.set_position_ms(target)
            self.mpris.emit_seeked(target)
        self._sync_send("seek", {"position_ms": target})

    def handle_mpris_set_position(self, position_ms):
        duration = self.player.get_duration()
        if duration <= 0:
            return
        target = max(0, min(int(position_ms), duration - 1))
        self.player.player.setPosition(target)
        if self.mpris:
            # Keep the polled Position accurate even while paused.
            self.mpris.set_position_ms(target)
            self.mpris.emit_seeked(target)
        self._sync_send("seek", {"position_ms": target})

    def handle_mpris_raise(self):
        window = self.window
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    def handle_mpris_quit(self):
        QApplication.quit()

    def handle_volume_mute(self):
        self.player.toggle_mute()

    def handle_stop(self):
        """Stop playback and tell synced-up peers."""
        self._sync_cancel_pending_advance()
        self.player.stop()
        self._sync_send("stop", {})

    def _resolve_url(self, item):
        if not item:
            return None
        return item.get("file_path") or item.get("url")

    def _playing_source(self):
        """Source of the track actually playing (or last played), or None
        while playback has never been started — the queue's anchor song
        must not pose as 'now playing' on a freshly opened playlist.

        Uses _playing_item, not _current_item: the anchor is re-pointed at
        the first song whenever a playlist that does not contain the playing
        track is opened, but the audible track must stay "now playing"."""
        if self.player._has_media:
            return self.player._playing_item
        return None

    def _publish_shuffle_state(self, enabled):
        """Mirror the shuffle state everywhere: bars, fullscreen, sort mode,
        MPRIS and the saved setting."""
        enabled = bool(enabled)
        self.window.set_shuffle_state(enabled)
        self.window.fullscreen_player.set_shuffle_state(enabled)
        self.window.queue_panel.set_sort_mode(
            "Shuffled" if enabled else "Date Added", reverse=True
        )
        set_app_setting("shuffle_enabled", str(enabled))
        if self.mpris:
            self.mpris.set_shuffle(enabled)

    def _sync_send_shuffle_state(self):
        """Broadcast the local shuffle state: the on/off flag, the exact seed
        it was shuffled with, the base queue and the queued-next sources.

        A synced-up machine that adopts all of that rebuilds byte-for-byte
        the same randomized order, so every machine plays the same upcoming
        tracks instead of drifting apart."""
        if not self.sync:
            return
        self._sync_send(
            "shuffle",
            {
                "enabled": self.player.shuffle_enabled,
                "seed": self.player._shuffle_seed,
                "current": self._sync_source_identity(self.player._current_item),
                "queue": [
                    self._sync_source_identity(u) for u in (self.player._base_queue or [])
                ],
                "next_sources": [
                    self._sync_source_identity(u) for u in (self.player._next_sources or [])
                ],
            },
        )

    def handle_shuffle_toggle(self, enabled):
        queue_urls = self.active_queue_urls or self.player._base_queue
        if not queue_urls:
            self.player.shuffle_enabled = False
            self._publish_shuffle_state(False)
            self._sync_send("shuffle", {"enabled": False})
            return

        current_item = self.player._current_item
        if current_item not in queue_urls:
            current_index = self.window.get_current_track_index()
            current_item = queue_urls[current_index] if 0 <= current_index < len(queue_urls) else queue_urls[0]

        self.player.set_queue(queue_urls, current_item=current_item)
        self.player.toggle_shuffle(enabled)
        self._publish_shuffle_state(self.player.shuffle_enabled)
        self.active_queue_urls = list(queue_urls)
        self._refresh_queue_display()
        self._sync_send_shuffle_state()
    
    def handle_load_playlists(self):
        """Load playlist list for browsing. Appends a synthetic \"All Songs\" playlist."""
        # Any in-flight search is stale now; don't let it replace this view.
        self._search_generation += 1
        QApplication.restoreOverrideCursor()
        self.current_playlist_id = None
        playlists = get_playlists()
        display_data = [
            {
                "title": playlist[1],
                "type": "playlist",
                "playlist_id": playlist[0],
                "count": playlist[3],
            }
            for playlist in playlists
        ]
        all_songs = get_all_playlist_songs_flat()
        display_data.append({
            "title": "All Songs",
            "type": "playlist",
            "playlist_id": "all",
            "count": len(all_songs),
        })
        self.current_results = display_data
        self._refresh_queue_display(reset_scroll=True)
        set_app_setting("last_view", "playlists")
        print(f"Loaded {len(playlists)} playlists + All Songs ({len(all_songs)} tracks)")

    def handle_import_list(self):
        """Ask for a Spotify URL and import it without blocking the UI."""
        if self.spotify_import_worker and self.spotify_import_worker.isRunning():
            return

        playlist_url, ok = QInputDialog.getText(
            self.window,
            "Import List",
            "Spotify playlist URL:",
            text="https://open.spotify.com/playlist/",
        )
        playlist_url = playlist_url.strip()
        if not ok or not playlist_url:
            return
        if "spotify.com/playlist/" not in playlist_url:
            QMessageBox.warning(
                self.window,
                "Import List",
                "Please enter a valid Spotify playlist URL.",
            )
            return

        # Ask whether to create a new playlist or add to an existing one
        target_playlist_id = None
        choice_box = QMessageBox(self.window)
        choice_box.setWindowTitle("Import Destination")
        choice_box.setText("Where should the songs go?")
        new_btn = choice_box.addButton("New Playlist", QMessageBox.AcceptRole)
        existing_btn = choice_box.addButton("Existing Playlist", QMessageBox.ActionRole)
        choice_box.addButton("Cancel", QMessageBox.RejectRole)
        choice_box.exec()
        clicked = choice_box.clickedButton()

        if clicked == existing_btn:
            playlists = get_playlists()
            if not playlists:
                QMessageBox.information(
                    self.window, "Import", "No existing playlists found. Creating a new one instead."
                )
            else:
                names = [p[1] for p in playlists]
                name, ok = QInputDialog.getItem(
                    self.window, "Select Playlist", "Add songs to:", names, 0, False
                )
                if not ok:
                    return
                for p in playlists:
                    if p[1] == name:
                        target_playlist_id = p[0]
                        break
        elif clicked is None or clicked != new_btn:
            return  # user cancelled

        self.spotify_import_worker = SpotifyImportWorker(playlist_url, target_playlist_id, self.window)
        self.spotify_import_worker.progress.connect(
            lambda current, total, title: print(f"[spotify] [{current}/{total}] {title}", flush=True)
        )
        self.spotify_import_worker.completed.connect(self._on_import_completed)
        self.spotify_import_worker.failed.connect(self._on_import_failed)
        self.window.sidebar.import_list_btn.setEnabled(False)
        self.spotify_import_worker.finished.connect(
            lambda: self.window.sidebar.import_list_btn.setEnabled(True)
        )
        self.spotify_import_worker.start()

    def _on_import_completed(self, playlist_id, failed_count, playlist_name, failure_log):
        self.handle_open_playlist(playlist_id)
        message = f'Imported "{playlist_name}".\nError log: {failure_log}'
        if failed_count:
            message += f" {failed_count} track(s) were logged as failed."
        self.window.show_notification(
            "Import finished",
            message,
            kind="warning" if failed_count else "success",
            duration_ms=7000,
        )

    def _on_import_failed(self, reason):
        self.window.show_notification(
            "Import failed",
            f"Import failed: {reason}",
            kind="error",
            duration_ms=7000,
        )

    def handle_link_spotify(self):
        """Connect a Spotify playlist to a program playlist and watch it at startup."""
        if self.spotify_link_worker and self.spotify_link_worker.isRunning():
            return

        playlist_url, ok = QInputDialog.getText(
            self.window,
            "Link Spotify",
            "Spotify playlist URL to watch for new songs:",
            text="https://open.spotify.com/playlist/",
        )
        playlist_url = playlist_url.strip()
        if not ok or not playlist_url:
            return
        if "spotify.com/playlist/" not in playlist_url:
            QMessageBox.warning(
                self.window,
                "Link Spotify",
                "Please enter a valid Spotify playlist URL.",
            )
            return

        # Ask which program playlist should receive the new songs.
        program_playlist_id = None
        choice_box = QMessageBox(self.window)
        choice_box.setWindowTitle("Link Destination")
        choice_box.setText("Which playlist should receive the new songs?")
        new_btn = choice_box.addButton("New Playlist", QMessageBox.AcceptRole)
        existing_btn = choice_box.addButton("Existing Playlist", QMessageBox.ActionRole)
        choice_box.addButton("Cancel", QMessageBox.RejectRole)
        choice_box.exec()
        clicked = choice_box.clickedButton()

        if clicked == existing_btn:
            playlists = get_playlists()
            if not playlists:
                QMessageBox.information(
                    self.window, "Link Spotify", "No existing playlists found. Use \"New Playlist\" instead."
                )
                return
            names = [p[1] for p in playlists]
            name, ok = QInputDialog.getItem(
                self.window, "Select Playlist", "Watch songs into:", names, 0, False
            )
            if not ok:
                return
            for p in playlists:
                if p[1] == name:
                    program_playlist_id = p[0]
                    break
        elif clicked == new_btn:
            name, ok = QInputDialog.getText(self.window, "Link Spotify", "Name for the new playlist:")
            name = name.strip()
            if not ok or not name:
                return
            row = create_playlist(name)
            if not row:
                QMessageBox.warning(
                    self.window, "Link Spotify", "Could not create the playlist (the name may already exist)."
                )
                return
            program_playlist_id = row[0]
        else:
            return  # user cancelled

        if program_playlist_id is None:
            return

        # The same Spotify list can feed many program playlists, but linking
        # the exact same pair twice would only duplicate the check.
        spotify_id = playlist_url.split("playlist/", 1)[1].split("?", 1)[0]
        for link in get_spotify_links():
            if link[1] == spotify_id and link[3] == program_playlist_id:
                QMessageBox.information(
                    self.window, "Link Spotify", "That Spotify playlist is already linked to this playlist."
                )
                return

        self.spotify_link_worker = SpotifyLinkWorker(playlist_url, program_playlist_id, self.window)
        self.spotify_link_worker.linked.connect(self._on_link_completed)
        self.spotify_link_worker.failed.connect(self._on_link_failed)
        self.window.sidebar.link_spotify_btn.setEnabled(False)
        self.spotify_link_worker.finished.connect(
            lambda: self.window.sidebar.link_spotify_btn.setEnabled(True)
        )
        self.spotify_link_worker.start()

    def _on_link_completed(self, program_playlist_id, spotify_name, snapshot_count):
        playlist_name = ""
        row = get_playlist_by_id(program_playlist_id)
        if row:
            playlist_name = row[1]
        QMessageBox.information(
            self.window,
            "Link Spotify",
            f'Linked Spotify list "{spotify_name}" to "{playlist_name}".\n\n'
            f"{snapshot_count} song(s) currently in the Spotify list were marked as known, "
            "so nothing was downloaded.\n"
            "Songs added to the Spotify list from now on are downloaded the next time "
            "the app starts.",
        )

    def _on_link_failed(self, reason):
        QMessageBox.warning(self.window, "Link Spotify", f"Link failed: {reason}")

    def start_spotify_sync(self):
        """Check every linked Spotify playlist once, right after startup."""
        if self.spotify_sync_worker and self.spotify_sync_worker.isRunning():
            return
        if not get_spotify_links():
            return
        self.spotify_sync_worker = SpotifySyncWorker(self.window)
        self.spotify_sync_worker.progress.connect(
            lambda current, total, title: print(f"[spotify-sync] [{current}/{total}] {title}", flush=True)
        )
        self.spotify_sync_worker.completed.connect(self._on_spotify_sync_completed)
        self.spotify_sync_worker.failed.connect(self._on_spotify_sync_failed)
        self.spotify_sync_worker.start()

    def _on_spotify_sync_completed(self, results):
        """Summarize the startup sync and refresh views that gained songs."""
        lines = []
        touched_ids = []
        blocked_total = 0
        had_problems = False
        for program_playlist_id, playlist_name, added, failed, blocked, _log in results:
            if not added and not failed and not blocked:
                continue
            touched_ids.append(program_playlist_id)
            line = f'"{playlist_name}": {added} new song(s) added'
            if failed:
                had_problems = True
                line += f", {failed} failed (see the Failed Songs button)"
            if blocked:
                had_problems = True
                blocked_total += blocked
                line += f", {blocked} blocked by YouTube's bot check (retried next start)"
            lines.append(line)
        if not lines:
            return  # nothing new anywhere; stay quiet

        self._refresh_current_view_for_playlists(touched_ids)

        self.window.show_notification(
            "Spotify sync finished",
            "\n".join(lines),
            kind="warning" if had_problems else "success",
            duration_ms=7000,
        )

        if blocked_total and not get_ytdlp_cookie_options():
            self._offer_cookie_setup(blocked_total)

    def _offer_cookie_setup(self, blocked_total):
        """After a bot-blocked sync, offer to remember a browser for cookies.

        YouTube's 'confirm you're not a bot' wall goes away once yt-dlp sends
        real browser cookies, so let the user pick their browser once; the
        choice is saved and used by every download from then on.
        """
        box = QMessageBox(self.window)
        box.setWindowTitle("YouTube Bot Check")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"YouTube blocked {blocked_total} song(s) with its 'confirm you're not a bot' "
            "sign-in wall.\n\n"
            "Pick the browser you are logged into YouTube with so the player can borrow its "
            "cookies.\nThis is saved once and used for every download from now on.\n"
            "The blocked songs are retried the next time the app starts."
        )
        browsers = [
            ("Firefox", "firefox"),
            ("Chrome", "chrome"),
            ("Edge", "edge"),
            ("Brave", "brave"),
            ("Chromium", "chromium"),
            ("Opera GX", "opera-gx"),
            ("Zen", "zen"),
        ]
        choices = [
            (box.addButton(label, QMessageBox.AcceptRole), value)
            for label, value in browsers
        ]
        skip_btn = box.addButton("Skip", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip_btn:
            return
        for button, value in choices:
            if button is clicked:
                set_ytdlp_cookie_config(browser=value)
                print(f"[spotify-sync] cookie source saved: {value}", flush=True)
                break

    def _on_spotify_sync_failed(self, reason):
        self.window.show_notification(
            "Spotify sync failed",
            f"Startup sync failed: {reason}",
            kind="error",
            duration_ms=7000,
        )

    def handle_view_failures(self):
        """Show the failed_songs.txt error log for a playlist."""
        playlists = get_playlists()
        if not playlists:
            QMessageBox.information(self.window, "Failed Songs", "No playlists exist yet.")
            return

        # Prefer the playlist the user is looking at, otherwise ask.
        target_id = self.current_playlist_id
        if target_id is None or target_id == "all" or not any(p[0] == target_id for p in playlists):
            names = [p[1] for p in playlists]
            name, ok = QInputDialog.getItem(
                self.window, "Failed Songs", "Show the error log for:", names, 0, False
            )
            if not ok:
                return
            target_id = next((p[0] for p in playlists if p[1] == name), None)
        if target_id is None:
            return

        row = get_playlist_by_id(target_id)
        if not row:
            return
        _, name, folder = row
        log_path = Path(folder) / "failed_songs.txt"
        text = ""
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            QMessageBox.information(self.window, "Failed Songs", f'No failed songs recorded for "{name}".')
            return

        # Keep the dialog manageable: show the most recent entries.
        lines = text.splitlines()
        shown = lines[-200:]
        dialog = QDialog(self.window)
        dialog.setWindowTitle(f"Failed Songs — {name}")
        dialog.resize(680, 480)
        layout = QVBoxLayout(dialog)
        editor = QTextEdit(dialog)
        editor.setReadOnly(True)
        editor.setPlainText("\n".join(shown))
        layout.addWidget(editor)
        if len(shown) < len(lines):
            editor.append(f"\n(showing the last {len(shown)} of {len(lines)} entries — full log: {log_path})")
        # Retry every song on the list, then keep only the ones that fail again
        buttons = QHBoxLayout()
        retry_btn = QPushButton("Retry Failed Songs", dialog)
        retry_btn.clicked.connect(
            lambda: self._start_retry_failed(target_id, name, folder, dialog)
        )
        buttons.addWidget(retry_btn)
        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        dialog.exec()

    def _start_retry_failed(self, playlist_id, playlist_name, folder, dialog):
        """Retry every song on a playlist's failure log without freezing the UI."""
        if self.retry_failed_worker and self.retry_failed_worker.isRunning():
            QMessageBox.information(
                self.window, "Retry Failed Songs",
                "A retry is already running. Please wait for it to finish."
            )
            return
        if self.add_song_worker and self.add_song_worker.isRunning():
            QMessageBox.information(
                self.window, "Retry Failed Songs",
                "Another song is still downloading. Please wait for it to finish."
            )
            return

        # Only lines with the "[timestamp] song: reason" format can be retried.
        log_path = Path(folder) / "failed_songs.txt"
        total = len(parse_failures(log_path))
        if not total:
            QMessageBox.information(
                self.window, "Retry Failed Songs",
                f'No retryable songs found on the Failed Songs list for "{playlist_name}".'
            )
            return

        dialog.accept()  # close the log; the progress dialog takes over

        worker = RetryFailedWorker(playlist_id, folder, self.window)
        self.retry_failed_worker = worker

        progress_dialog = QProgressDialog(
            f"Retrying 1/{total} …", "Cancel", 0, total, self.window
        )
        progress_dialog.setWindowTitle(f"Retry Failed Songs — {playlist_name}")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setAutoClose(False)
        progress_dialog.setAutoReset(False)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.canceled.connect(worker.requestInterruption)
        progress_dialog.show()

        def on_retry_progress(current, count, song):
            progress_dialog.setValue(max(current - 1, 0))
            progress_dialog.setLabelText(f"Retrying {current}/{count}:\n{song}")

        worker.progress.connect(on_retry_progress)
        worker.completed.connect(
            lambda summary: self._on_retry_completed(
                summary, playlist_id, playlist_name, progress_dialog
            )
        )
        worker.failed.connect(
            lambda reason: self._on_retry_failed(reason, progress_dialog)
        )
        self.window.sidebar.view_failures_btn.setEnabled(False)
        worker.finished.connect(
            lambda: self.window.sidebar.view_failures_btn.setEnabled(True)
        )
        worker.start()

    def _on_retry_completed(self, summary, playlist_id, playlist_name, progress_dialog):
        """Summarize a finished retry run; the log already holds the failures."""
        progress_dialog.close()

        downloaded = summary["downloaded"]
        still_failed = summary["still_failed"]
        if downloaded:
            self._refresh_current_view_for_playlists([playlist_id])

        lines = [f'Retry finished for "{playlist_name}".']
        lines.append(f"{len(downloaded)} song(s) downloaded.")
        if still_failed:
            lines.append(
                f"{len(still_failed)} song(s) failed again — they were put back "
                "on the Failed Songs list."
            )
        else:
            lines.append("No songs failed this time — the Failed Songs list was cleared.")
        if summary["not_retried"]:
            lines.append(f"{summary['not_retried']} song(s) were not retried and stayed on the list.")
        self.window.show_notification(
            "Retry finished",
            "\n".join(lines),
            kind="warning" if still_failed or summary["not_retried"] else "success",
            duration_ms=7000,
        )

        if summary["bot_blocked"] and not get_ytdlp_cookie_options():
            self._offer_cookie_setup(len(still_failed) or 1)

    def _on_retry_failed(self, reason, progress_dialog):
        progress_dialog.close()
        self.window.show_notification(
            "Retry failed",
            f"Retry failed: {reason}",
            kind="error",
            duration_ms=7000,
        )


    def handle_add_to_playlist(self, item=None):
        """Add selected song to an existing or new playlist."""
        item = item or self.window.get_selected_queue_item()
        print(f"[playlist-add] selected item: {item!r}", flush=True)
        if not item:
            print("[playlist-add] aborted: no selected item", flush=True)
            return
        if item.get("type") == "playlist":
            print("[playlist-add] aborted: selected item is a playlist", flush=True)
            QMessageBox.information(self.window, "Add to Playlist", "Please select a track to add to a playlist.")
            return

        track_title = item.get("title")
        track_url = item.get("url") or item.get("file_path")
        if not track_title or not track_url:
            print(
                f"[playlist-add] aborted: missing title or URL; title={track_title!r}, url={track_url!r}",
                flush=True,
            )
            QMessageBox.warning(
                self.window,
                "Add to Playlist",
                "The selected song has no usable title or file path.",
            )
            return
        playlist_id = self._choose_playlist()
        if not playlist_id:
            print("[playlist-add] aborted: no destination playlist selected", flush=True)
            return

        # One download at a time: a second request while one is in flight
        # would double-download and confuse the dialogs.
        if self.add_song_worker and self.add_song_worker.isRunning():
            self.window.show_notification(
                "Download in progress",
                "Another song is still downloading. Please wait for it to finish.",
                kind="info",
            )
            return

        print(
            f"[playlist-add] starting: title={track_title!r}, url={track_url!r}, playlist_id={playlist_id}",
            flush=True,
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.add_song_worker = AddSongWorker(
            track_title,
            track_url,
            playlist_id,
            artist=item.get("artist"),
            thumbnail=item.get("thumbnail"),
        )
        self.add_song_worker.add_finished.connect(
            lambda path, title=track_title, pid=playlist_id: self._on_add_song_finished(title, path, pid)
        )
        self.add_song_worker.add_failed.connect(self._on_add_song_failed)
        self.add_song_worker.start()

    def _on_add_song_finished(self, title, saved_path, playlist_id):
        QApplication.restoreOverrideCursor()
        print(f"[playlist-add] success: {saved_path}", flush=True)
        # Show the new song in the open view right away when it belongs there,
        # instead of waiting for the user to reopen the playlist.
        self._refresh_current_view_for_playlists([playlist_id])
        self.window.show_notification(
            "Download complete",
            f'Added "{title}" to playlist.\n{saved_path}',
            kind="success",
        )

    def _on_add_song_failed(self, reason):
        QApplication.restoreOverrideCursor()
        print(f"[playlist-add] failed: {reason}", flush=True)
        self.window.show_notification(
            "Download failed",
            f"Failed to add song to playlist.\n{reason}",
            kind="error",
            duration_ms=7000,
        )

    def _choose_playlist(self):
        playlists = get_playlists()
        playlist_names = [p[1] for p in playlists]
        playlist_ids = [p[0] for p in playlists]

        playlist_names.append("<Create New Playlist>")

        choice, ok = QInputDialog.getItem(
            self.window,
            "Choose Playlist",
            "Select playlist or create a new one:",
            playlist_names,
            0,
            False,
        )
        if not ok or not choice:
            return None

        if choice == "<Create New Playlist>":
            playlist_name, ok = QInputDialog.getText(
                self.window,
                "New Playlist",
                "Enter playlist name:",
            )
            if not ok or not playlist_name.strip():
                return None
            playlist = create_playlist(playlist_name.strip())
            return playlist[0] if playlist else None

        index = playlist_names.index(choice)
        if index >= 0 and index < len(playlist_ids):
            return playlist_ids[index]
        return None

    def handle_volume_change(self, value):
        """Update player volume and remember it in the database"""
        self.player.set_volume(value)
        self.window.set_volume(value)
        self.window.fullscreen_player.set_volume(value)
        set_app_setting("volume", int(value))
        if self.mpris:
            self.mpris.set_volume(max(0, min(int(value), 100)) / 100.0)

    def handle_volume_up(self):
        """Bump volume up by 5."""
        if self.window._touch_mode:
            current = self.window.touch_player_bar.wedge_volume.value()
        else:
            current = self.window.player_bar.volume_slider.value()
        self.handle_volume_change(min(100, current + 5))

    def handle_volume_down(self):
        """Bump volume down by 5."""
        if self.window._touch_mode:
            current = self.window.touch_player_bar.wedge_volume.value()
        else:
            current = self.window.player_bar.volume_slider.value()
        self.handle_volume_change(max(0, current - 5))

    def handle_seek(self, position):
        """Seek to position (0.0-1.0)"""
        self.player.seek(position)
        if self.mpris:
            self.mpris.emit_seeked(self.player.get_position())
        self._sync_send("seek", {"position_ms": self.player.get_position()})

    def handle_seek_delta(self, seconds):
        """Move playback by a fixed number of seconds."""
        duration = self.player.get_duration()
        if duration <= 0:
            return
        position = self.player.get_position() + int(seconds * 1000)
        self.player.seek(max(0.0, min(position / duration, 1.0)))
        if self.mpris:
            self.mpris.emit_seeked(self.player.get_position())
        self._sync_send("seek", {"position_ms": self.player.get_position()})

    def handle_fullscreen(self):
        fullscreen = self.window.fullscreen_player
        if fullscreen.isVisible():
            fullscreen.close()
        else:
            screen = self._fullscreen_target_screen()
            if screen is not None:
                # Pin the fullscreen view to the screen picked in Settings.
                # setScreen only sticks while the window is hidden, which is
                # exactly the case here; the geometry keeps frameless windows
                # honest on some window managers.
                fullscreen.setScreen(screen)
                fullscreen.setGeometry(screen.geometry())
            fullscreen.showFullScreen()
            fullscreen.raise_()
            fullscreen.activateWindow()
            self._update_now_playing()

    def _fullscreen_target_screen(self):
        """QScreen the fullscreen view should fill, or None for "Auto".

        The setting stores the QScreen name (e.g. "DP-1"); a missing or
        unplugged screen silently falls back to the window's own screen.
        """
        name = (get_app_setting("fullscreen_screen", "auto") or "auto").strip()
        if name and name.lower() != "auto":
            for screen in QApplication.screens():
                if screen.name() == name:
                    return screen
        return None

    def _queue_scale(self):
        """Saved queue row scale, clamped to the slider range."""
        try:
            return max(0.5, min(3.0, float(get_app_setting("queue_row_scale", 1.0) or 1.0)))
        except (TypeError, ValueError):
            return 1.0

    def _apply_queue_scale(self, scale=None):
        """Apply a row scale to the queue view (default: the saved one)."""
        if scale is None:
            scale = self._queue_scale()
        self.window.queue_panel.set_row_scale(scale)

    def handle_settings(self):
        """Open the settings dialog (fullscreen screen + queue row size)."""
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Settings")
        dialog.setMinimumWidth(520)
        # The app stylesheet themes most widgets but not QComboBox; give the
        # dialog's dropdown the same dark look inline.
        dialog.setStyleSheet(
            "QComboBox { background-color: #1e1e1e; color: #ffffff;"
            " border: 1px solid #404040; border-radius: 4px; padding: 8px; }"
            "QComboBox:focus { border: 1px solid #1db954; }"
            "QComboBox::drop-down { border: none; width: 26px; }"
            "QComboBox QAbstractItemView { background-color: #1e1e1e;"
            " color: #ffffff; selection-background-color: #1db954;"
            " selection-color: #ffffff; outline: none; }"
        )
        layout = QVBoxLayout(dialog)

        # ── Fullscreen screen ──
        layout.addWidget(QLabel("Fullscreen shows on:", dialog))
        screen_combo = QComboBox(dialog)
        screens = QApplication.screens()
        saved_screen = (get_app_setting("fullscreen_screen", "auto") or "auto").strip()
        screen_combo.addItem("Auto (screen the window is on)", "auto")
        selected = 0
        for i, screen in enumerate(screens):
            geo = screen.geometry()
            name = screen.name() or f"Screen {i + 1}"
            label = f"{name}  ({geo.width()}x{geo.height()})"
            if screen == QApplication.primaryScreen():
                label += " — primary"
            screen_combo.addItem(label, screen.name())
            if screen.name() == saved_screen:
                selected = screen_combo.count() - 1
        screen_combo.setCurrentIndex(selected)

        def save_screen(index):
            set_app_setting("fullscreen_screen", str(screen_combo.itemData(index) or "auto"))

        screen_combo.activated.connect(save_screen)
        layout.addWidget(screen_combo)
        screen_hint = QLabel(
            "Picks the monitor the fullscreen player fills. If that screen "
            "is ever unplugged, fullscreen falls back to the window's own screen.",
            dialog,
        )
        screen_hint.setWordWrap(True)
        layout.addWidget(screen_hint)

        layout.addSpacing(12)

        # ── Queue row size ──
        scale_row = QHBoxLayout()
        scale_label = QLabel("Queue song size:", dialog)
        scale_row.addWidget(scale_label)
        percent_label = QLabel(dialog)
        scale_row.addWidget(percent_label)
        scale_row.addStretch()
        layout.addLayout(scale_row)

        scale_slider = QSlider(Qt.Horizontal, dialog)
        scale_slider.setRange(50, 300)  # percent of the default row size
        scale_slider.setValue(int(round(self._queue_scale() * 100)))
        scale_slider.setTickPosition(QSlider.TicksBelow)
        scale_slider.setTickInterval(25)
        scale_slider.setSingleStep(5)
        scale_slider.setPageStep(25)

        def on_scale_changed(value):
            scale = value / 100.0
            percent_label.setText(f"{value}%")
            self._apply_queue_scale(scale)
            set_app_setting("queue_row_scale", str(scale))

        percent_label.setText(f"{scale_slider.value()}%")
        scale_slider.valueChanged.connect(on_scale_changed)
        layout.addWidget(scale_slider)
        scale_hint = QLabel(
            "Scales the song rows in the queue view (icon, text and spacing) "
            "live while you drag. Works in both normal and touch panel mode.",
            dialog,
        )
        scale_hint.setWordWrap(True)
        layout.addWidget(scale_hint)

        layout.addSpacing(12)

        # ── Buttons ──
        buttons = QHBoxLayout()
        reset_btn = QPushButton("Reset Defaults", dialog)

        def reset_defaults():
            screen_combo.setCurrentIndex(0)
            save_screen(0)
            scale_slider.setValue(100)

        reset_btn.clicked.connect(reset_defaults)
        buttons.addWidget(reset_btn)
        buttons.addStretch()
        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        dialog.exec()

    def handle_back(self):
        """Back button: exit fullscreen, clear highlight, or go to playlists list."""
        if self.window.fullscreen_player.isVisible():
            self.window.fullscreen_player.close()
            return
        # Return/Back: exit layer-2 modes first, fall through to playlist navigation
        self.window.on_return_pressed()
        # If no layer-2 mode was active, on_return_pressed cleared highlight but
        # didn't change view — fall through to playlist navigation
        if self.window._remote_highlighted is None and self.current_playlist_id is not None:
            self.handle_load_playlists()
            return

    def shutdown(self): 
        """Stop background work before Qt destroys the application."""
        self._stop_sync_engine()
        if self.metadata_fetcher and self.metadata_fetcher.isRunning():
            self.metadata_fetcher.request_stop()
            self.metadata_fetcher.wait(3000)
        for fetcher in self._retired_fetchers:
            fetcher.wait(1000)
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.wait(2000)
        for worker in self._retired_search_workers:
            worker.wait(1000)
        if self.add_song_worker and self.add_song_worker.isRunning():
            self.add_song_worker.wait(3000)
        if self.spotify_import_worker and self.spotify_import_worker.isRunning():
            # quit() is a no-op for a plain run() loop; ask for a
            # cooperative stop between tracks and cap the wait so a long
            # import can never keep the app from closing.
            self.spotify_import_worker.requestInterruption()
            self.spotify_import_worker.wait(3000)
        if self.spotify_link_worker and self.spotify_link_worker.isRunning():
            self.spotify_link_worker.wait(3000)
        if self.spotify_sync_worker and self.spotify_sync_worker.isRunning():
            self.spotify_sync_worker.requestInterruption()
            self.spotify_sync_worker.wait(3000)
        if self.retry_failed_worker and self.retry_failed_worker.isRunning():
            # Same cooperative stop between songs as the Spotify workers.
            self.retry_failed_worker.requestInterruption()
            self.retry_failed_worker.wait(3000)
        if self.cec_remote and self.cec_remote.isRunning():
            self.cec_remote.stop()
        if self.gamepad:
            self.gamepad.stop()
        if self.mpris:
            self.mpris.shutdown()
        self._stop_sync_engine()
        self.player.stop()
        # Wait for in-flight background stream resolves so their QThreads
        # are not destroyed while running.
        self.player.shutdown()

    # ---------- network sync (multiroom playback) ----------

    def _sync_configured_peers(self):
        """Peer MAC addresses from the app settings, normalized."""
        raw = get_app_setting("sync_peers", "") or ""
        peers = []
        for part in raw.replace(";", ",").replace(" ", ",").split(","):
            mac = normalize_mac(part)
            if mac and mac not in peers:
                peers.append(mac)
        return peers

    def _start_sync_engine(self):
        """Start the sync engine when enabled with at least one peer."""
        if self.sync:
            return
        enabled = (get_app_setting("sync_enabled", "False") or "").lower() == "true"
        peers = self._sync_configured_peers()
        if not enabled or not peers:
            return
        try:
            port_text = get_app_setting("sync_port") or str(DEFAULT_SYNC_PORT)
            self.sync = SyncEngine(peers, int(port_text))
        except Exception as error:
            print(f"[sync] engine unavailable: {error}", flush=True)
            self.sync = None
            return
        self._sync_own_id = self.sync.own_id
        self.sync.remote_command.connect(self._on_sync_remote_command)
        self.sync.peer_status.connect(self._on_sync_peer_status)
        self.sync.start()
        self._sync_state_timer.start()
        print(f"[sync] engine started (this machine: {self._sync_own_id})", flush=True)

    def _stop_sync_engine(self):
        if not self.sync:
            return
        self._sync_state_timer.stop()
        self._sync_cancel_pending_advance()
        try:
            self.sync.stop()
        except Exception:
            pass
        self.sync = None
        self._sync_leader_id = None
        self._sync_leader_state = None
        self._sync_peer_states = {}
        self._sync_peer_finished = {}

    @contextlib.contextmanager
    def _sync_applying_remote(self):
        """Wrap a remote-command application so it is not re-broadcast."""
        self._sync_suppress_depth += 1
        try:
            yield
        finally:
            self._sync_suppress_depth -= 1

    def _sync_send(self, command, payload=None):
        """Broadcast a local action to synced-up peers.

        Also claims leadership: the last machine to act is the one every
        other machine follows. Suppressed while applying a remote command
        so nothing echoes back and forth.
        """
        if not self.sync or self._sync_suppress_depth > 0:
            return
        self._sync_leader_id = self._sync_own_id
        self._sync_last_local_command = time.monotonic()
        self.sync.send_command(command, payload or {})

    def _sync_after_track_change(self, before):
        """After next/prev/autoplay: tell peers what is playing now.

        The broadcast carries the full play order too, so a machine whose
        queue had drifted apart re-adopts ours and both stay on the same
        upcoming tracks.
        """
        after = self._playing_source()
        if after and after != before:
            payload = self._sync_play_payload(self.player.get_position())
            if payload:
                self._sync_send("play", payload)

    def _sync_track_info(self, position_ms):
        """Describe what is playing right now, for a sync broadcast.

        The file path is sent in its portable (relative) form so it points
        at the right file on every machine; the source URL is included so
        machines without the file can stream the track instead.
        """
        source = self._playing_source()
        if not source:
            return None

        # Find the queue item dict so remote machines get the URL too.
        item = None
        pools = []
        panel = self.window.queue_panel
        if getattr(panel, "items_data", None):
            pools.extend(panel.items_data)
        if getattr(panel, "master_items", None):
            pools.extend(panel.master_items)
        pools.extend(self.current_results)
        for candidate in pools:
            if isinstance(candidate, dict) and source in (
                candidate.get("file_path"), candidate.get("url")
            ):
                item = candidate
                break

        file_path = None
        url = None
        title = None
        artist = None
        if item:
            file_path = item.get("file_path")
            url = item.get("url")
            title = item.get("title")
            artist = item.get("artist")
        if not file_path and source and not source.startswith("http") and Path(source).exists():
            file_path = source
        if not url and source.startswith("http"):
            url = source
        if file_path:
            metadata = get_track_metadata(file_path)
            url = url or metadata.get("url")
            title = title or metadata.get("title")
            artist = artist or metadata.get("artist")

        return {
            "file": to_portable_path(file_path) if file_path else None,
            "url": url,
            "title": title or Path(source).name,
            "artist": artist,
            "position_ms": int(position_ms or 0),
        }

    def _sync_item_pools(self):
        """Every item dict that might describe a queued track."""
        pools = []
        panel = self.window.queue_panel
        if getattr(panel, "items_data", None):
            pools.extend(panel.items_data)
        if getattr(panel, "master_items", None):
            pools.extend(panel.master_items)
        pools.extend(self.current_results)
        return pools

    def _sync_source_identity(self, source, item_by_source=None):
        """{file, url} identity of a queue source, portable across machines.

        The file path is stored in its portable form so it points at the
        right file on every machine; the URL lets machines without the file
        stream the track instead of dropping it from the queue.
        """
        pair = {"file": None, "url": None}
        if not source:
            return pair
        cached = self._sync_identity_cache.get(source)
        if cached is not None:
            return dict(cached)
        if str(source).startswith("http"):
            pair["url"] = source
        else:
            pair["file"] = to_portable_path(str(source))
            item = None
            if item_by_source is not None:
                item = item_by_source.get(source)
            else:
                for candidate in self._sync_item_pools():
                    if isinstance(candidate, dict) and source in (
                        candidate.get("file_path"), candidate.get("url")
                    ):
                        item = candidate
                        break
            url = item.get("url") if isinstance(item, dict) else None
            if not url:
                try:
                    url = get_track_metadata(str(source)).get("url")
                except Exception:
                    url = None
            pair["url"] = url or None
        self._sync_identity_cache[source] = dict(pair)
        return dict(pair)

    def _sync_source_from_identity(self, pair):
        """Map a peer's {file, url} identity to a source playable here:
        our local copy of the file, or the stream URL as a fallback."""
        if not isinstance(pair, dict):
            return None
        file_rel = pair.get("file")
        url = pair.get("url")
        if file_rel:
            file_path = resolve_stored_path(file_rel)
            if file_path and Path(file_path).exists():
                return file_path
        if url:
            return url
        return None

    def _sync_queue_payload(self):
        """The full play order plus the queue position, for broadcasts.

        Sent along with every "play" so a synced-up machine that adopts the
        broadcast ends up with the exact same upcoming tracks as the sender,
        even if the two queues had drifted apart before."""
        item_by_source = {}
        for item in self._sync_item_pools():
            if not isinstance(item, dict):
                continue
            source = item.get("file_path") or item.get("url")
            if source and source not in item_by_source:
                item_by_source[source] = item
        queue = [
            self._sync_source_identity(u, item_by_source)
            for u in (self.player.queue or [])
        ]
        next_sources = [
            self._sync_source_identity(u, item_by_source)
            for u in (self.player._next_sources or [])
        ]
        return {
            "queue": queue,
            "queue_index": self.player.index,
            "next_sources": next_sources,
        }

    def _sync_play_payload(self, position_ms):
        """Track info plus the full play order, in one broadcast."""
        info = self._sync_track_info(position_ms)
        if not info:
            return None
        info.update(self._sync_queue_payload())
        return info

    def _sync_adopt_queue_payload(self, payload):
        """Replace our play order with a peer's (carried by play broadcasts).

        Returns True when the order was adopted. Songs this machine cannot
        map (file missing here and no stream URL) are skipped.
        """
        pairs = payload.get("queue")
        if not isinstance(pairs, list) or not pairs:
            return False
        sources = [self._sync_source_from_identity(p) for p in pairs]
        sources = [s for s in sources if s]
        if not sources:
            return False
        next_sources = [
            self._sync_source_from_identity(p)
            for p in (payload.get("next_sources") or [])
        ]
        next_sources = [s for s in next_sources if s]
        try:
            index = int(payload.get("queue_index") or 0)
        except (TypeError, ValueError):
            index = 0
        self.player.adopt_synced_queue(sources, index, next_sources)
        self.active_queue_urls = list(self.player.queue)
        self._refresh_queue_display()
        return True

    def _sync_apply_shuffle(self, payload):
        """Adopt a peer's shuffle toggle: same on/off state, same seed, same
        base queue — therefore the exact same upcoming order here."""
        enabled = bool(payload.get("enabled"))
        sources = []
        pairs = payload.get("queue")
        if isinstance(pairs, list):
            sources = [self._sync_source_from_identity(p) for p in pairs]
            sources = [s for s in sources if s]
        if not sources:
            # No queue context came along — just mirror the on/off flag.
            if self.player.shuffle_enabled != enabled:
                self.player.toggle_shuffle(enabled)
            self._publish_shuffle_state(self.player.shuffle_enabled)
            return
        current = self._sync_source_from_identity(payload.get("current"))
        next_sources = [
            self._sync_source_from_identity(p)
            for p in (payload.get("next_sources") or [])
        ]
        next_sources = [s for s in next_sources if s]
        self.player.apply_synced_shuffle(
            enabled,
            payload.get("seed"),
            sources,
            current_item=current,
            next_sources=next_sources,
        )
        self.active_queue_urls = list(self.player.queue)
        self._publish_shuffle_state(self.player.shuffle_enabled)
        self._refresh_queue_display()

    def _sync_apply_queue_next(self, payload):
        """Insert a track a peer queued as its next one right after ours."""
        source = self._sync_source_from_identity(payload)
        if not source:
            return
        self.player.queue_next(source)
        self.active_queue_urls = list(self.player.queue)
        self._refresh_queue_display()

    def _sync_send_state(self):
        """Periodically tell peers what is playing (every machine, both sides).

        The heartbeat is informational: receivers do not chase its position.
        It lets each machine know which track the peer is on (so track ends
        can be coordinated) and lets an idle machine join a playing peer.
        """
        if not self.sync:
            return
        if not any(
            str(status).startswith("connected")
            for status in self.sync.peer_statuses().values()
        ):
            return
        source = self._playing_source()
        if not source:
            return
        state = self._sync_track_info(self.player.get_position())
        if not state:
            return
        state["playing"] = (
            self.player.player.playbackState() == self.player.player.PlaybackState.PlayingState
        )
        self.sync.send_command("state", state)

    def _on_sync_remote_command(self, command, payload):
        """Apply a command from a synced-up peer (runs on the UI thread)."""
        sender = payload.get("sender")
        if not sender or sender == self._sync_own_id or not self.sync:
            return

        if command == "state":
            # Every machine heartbeats its state; keep the freshest record
            # for each peer (or adopt a leader when nobody claimed it yet).
            self._sync_peer_states[sender] = (dict(payload), time.monotonic())
            if self._sync_leader_id is None and payload.get("playing"):
                self._sync_leader_id = sender
            if self._sync_leader_id == sender:
                self._sync_leader_state = dict(payload)
                self._sync_leader_received_at = time.monotonic()
            # A machine that has never played anything joins a peer that is
            # already playing (one-shot; no continuous position chasing).
            if (
                payload.get("playing")
                and not self.player._has_media
                and time.monotonic() - self._sync_last_join_attempt > 5.0
            ):
                self._sync_last_join_attempt = time.monotonic()
                with self._sync_applying_remote():
                    self._sync_leader_id = sender
                    payload["playing"] = True
                    self._sync_leader_state = dict(payload)
                    self._sync_leader_received_at = time.monotonic()
                    self._sync_apply_play(payload)
            return

        if command == "finished":
            record = {
                "url": payload.get("url"),
                "file": resolve_stored_path(payload.get("file")) if payload.get("file") else None,
                "at": time.monotonic(),
            }
            self._sync_peer_finished[sender] = record
            pending = self._sync_pending_advance
            if pending is not None and self._sync_same_track(
                (pending.get("url"), pending.get("file")), (record["url"], record["file"])
            ):
                self._sync_resolve_pending_advance()
            return

        # Two machines acted at the same moment: the smaller MAC wins, the
        # other keeps its own action.
        if (time.monotonic() - self._sync_last_local_command) < 0.3 and sender > (self._sync_own_id or ""):
            return

        self._sync_leader_id = sender
        with self._sync_applying_remote():
            if command == "play":
                payload["playing"] = True
                self._sync_leader_state = dict(payload)
                self._sync_leader_received_at = time.monotonic()
                self._sync_apply_play(payload)
            elif command == "pause":
                self.player.pause()
                if self._sync_leader_state is not None:
                    self._sync_leader_state["playing"] = False
                    self._sync_leader_state["position_ms"] = int(payload.get("position_ms") or 0)
            elif command == "resume":
                self.player.resume()
                if self._sync_leader_state is not None:
                    self._sync_leader_state["playing"] = True
            elif command == "seek":
                duration = self.player.get_duration()
                target = int(payload.get("position_ms") or 0)
                if duration > 0:
                    self.player.player.setPosition(max(0, min(target, duration - 1)))
                if self._sync_leader_state is not None:
                    self._sync_leader_state["position_ms"] = target
                    self._sync_leader_received_at = time.monotonic()
            elif command == "stop":
                self.player.stop()
                self._sync_leader_state = None
            elif command == "shuffle":
                self._sync_apply_shuffle(payload)
            elif command == "queue_next":
                self._sync_apply_queue_next(payload)

    def _sync_apply_play(self, payload):
        """Start the track a peer is playing.

        Switches and seeks sync both machines from that point on ONCE: the
        incoming position is applied a single time once the media has
        loaded, and afterwards every machine plays at its own pace (no
        continuous drift correction).
        """
        file_rel = payload.get("file")
        url = payload.get("url")
        title = payload.get("title") or "Synced track"
        position_ms = int(payload.get("position_ms") or 0)

        file_path = resolve_stored_path(file_rel) if file_rel else None
        if file_path and not Path(file_path).exists():
            file_path = None  # this machine does not have that file
        if not file_path and not url:
            self.window.show_notification(
                "Network sync",
                f'The synced machine is playing "{title}", '
                "which is not in this library.",
                kind="warning",
                duration_ms=5000,
            )
            return

        # Adopt the peer's full play order when it came along, so both
        # machines advance through the same upcoming tracks from here on —
        # even if this queue had something else lined up before.
        self._sync_adopt_queue_payload(payload)

        # Already playing this exact track: both machines advance themselves
        # at a track's end and then tell each other, so this broadcast must
        # not restart the track that is already playing.
        incoming = (url, file_path)
        if self._sync_same_track(self._sync_identity(self._playing_source()), incoming):
            return

        generation = self.player._play_generation
        item = {"type": "track", "title": title}
        if file_path:
            item["file_path"] = file_path
        if url:
            item["url"] = url
        self._play_item(item)

        # One-shot position alignment once the media has loaded (instant for
        # local files, after the stream resolve for remote URLs).
        if position_ms > 3000:
            self._sync_seek_once_loaded(position_ms, generation)

    def _sync_seek_once_loaded(self, target_ms, generation_before, attempts=50):
        """Seek to a synced position a single time, once duration is known.

        Gives up when the user starts a different track in the meantime.
        """
        generation = self.player._play_generation
        if generation > generation_before + 1:
            return  # a newer track started while we were waiting
        duration = self.player.get_duration()
        if duration > 0 and generation > generation_before:
            with self._sync_applying_remote():
                self.player.player.setPosition(max(0, min(int(target_ms), duration - 1)))
            return
        if attempts > 0:
            QTimer.singleShot(
                200,
                lambda: self._sync_seek_once_loaded(target_ms, generation_before, attempts - 1),
            )

    def _sync_identity(self, source):
        """(url, file_path) identity of a playing source, or (None, None)."""
        if not source:
            return (None, None)
        if source.startswith("http"):
            return (source, None)
        if Path(source).exists():
            return (get_track_metadata(source).get("url"), source)
        return (None, source)

    def _sync_same_track(self, mine, theirs):
        my_url, my_file = mine
        their_url, their_file = theirs
        if my_file and their_file and my_file == their_file:
            return True
        if my_url and their_url and my_url == their_url:
            return True
        return False

    # ---------- end-of-track coordination ----------

    def _sync_peer_playing_same(self, identity):
        """True while a connected peer is playing the same track right now."""
        mine = (identity[0], identity[1])
        now = time.monotonic()
        for state, received_at in self._sync_peer_states.values():
            if now - received_at > 10.0 or not state.get("playing"):
                continue
            theirs = (
                state.get("url"),
                resolve_stored_path(state.get("file")) if state.get("file") else None,
            )
            if self._sync_same_track(mine, theirs):
                return True
        return False

    def _sync_peer_finished_same(self, identity):
        """True if a peer already announced finishing this same track."""
        mine = (identity[0], identity[1])
        now = time.monotonic()
        for record in self._sync_peer_finished.values():
            if now - record.get("at", 0.0) > 60.0:
                continue
            if self._sync_same_track(mine, (record.get("url"), record.get("file"))):
                return True
        return False

    def _sync_wait_for_peer_finish(self, identity):
        """Hold the track transition until the peer finishes too (or times out)."""
        self._sync_pending_advance = {
            "url": identity[0],
            "file": identity[1],
            "at": time.monotonic(),
        }
        self._sync_pending_timer.start()

    def _sync_resolve_pending_advance(self):
        """Run the held track transition (the peer finished, or we gave up)."""
        pending = self._sync_pending_advance
        if pending is None:
            return
        self._sync_pending_advance = None
        self._sync_pending_timer.stop()
        current = self._sync_identity(self._playing_source())
        if self._sync_same_track(current, (pending.get("url"), pending.get("file"))):
            self._advance_after_track_end()

    def _sync_pending_advance_timeout(self):
        """The peer never reported finishing this track — move on anyway."""
        self._sync_resolve_pending_advance()

    def _sync_cancel_pending_advance(self):
        """Drop a held track transition (the user acted manually)."""
        self._sync_pending_advance = None
        self._sync_pending_timer.stop()

    def _on_sync_peer_status(self, mac, status):
        """Track and surface peer connection changes."""
        print(f"[sync] {mac}: {status}", flush=True)
        was = self._sync_peer_status.get(mac)
        self._sync_peer_status[mac] = status
        connected = str(status).startswith("connected")
        if was is not None and str(was).startswith("connected") != connected:
            self.window.show_notification(
                "Network sync",
                f"{mac}: {status}",
                kind="success" if connected else "warning",
                duration_ms=4000,
            )
        if not connected and self._sync_leader_id == mac:
            self._sync_leader_id = None
            self._sync_leader_state = None
        if not connected:
            self._sync_peer_states.pop(mac, None)
            self._sync_peer_finished.pop(mac, None)
            # A peer that vanished mid-track must not hold our transition.
            if self._sync_pending_advance is not None:
                self._sync_resolve_pending_advance()
        if self._sync_dialog_refresh:
            self._sync_dialog_refresh()

    def handle_network_sync(self):
        """Open the network-sync settings dialog."""
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Network Sync")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)

        enabled_check = QCheckBox("Sync playback with other machines", dialog)
        enabled_check.setChecked((get_app_setting("sync_enabled", "False") or "").lower() == "true")
        layout.addWidget(enabled_check)

        layout.addWidget(QLabel("Peer MAC addresses (comma separated):", dialog))
        peers_edit = QLineEdit(get_app_setting("sync_peers", "") or "", dialog)
        peers_edit.setPlaceholderText("e.g. aa:bb:cc:dd:ee:01, aa:bb:cc:dd:ee:02")
        layout.addWidget(peers_edit)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:", dialog))
        port_edit = QLineEdit(str(get_app_setting("sync_port") or DEFAULT_SYNC_PORT), dialog)
        port_edit.setMaximumWidth(90)
        port_row.addWidget(port_edit)
        port_row.addStretch()
        layout.addLayout(port_row)

        own_label = QLabel(
            f"This machine: {self._sync_own_id or local_mac()}\n"
            "Type that address into the other machine's peer list.",
            dialog,
        )
        own_label.setWordWrap(True)
        layout.addWidget(own_label)

        status_label = QLabel(dialog)
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        def refresh_status():
            peers = self._sync_configured_peers()
            if not self.sync:
                if (get_app_setting("sync_enabled", "False") or "").lower() == "true" and peers:
                    lines = ["Engine is not running — press Save & Apply."]
                else:
                    lines = ["Sync is disabled."]
            else:
                statuses = self.sync.peer_statuses()
                lines = [
                    f"{mac}: {statuses.get(mac, 'waiting…')}" for mac in peers
                ] or ["No peers configured yet."]
            status_label.setText("\n".join(lines))

        refresh_status()
        self._sync_dialog_refresh = refresh_status

        hint = QLabel(
            "Both machines must list each other's MAC. The player keeps trying "
            f"to reach each MAC in the background (port {DEFAULT_SYNC_PORT} by default); "
            "when both apps run they sync up automatically and either one can "
            "trigger the other — play, pause, seek and track changes stay "
            "together. Tracks are matched by the synced library files, and a "
            "machine without the file streams it by its URL instead.",
            dialog,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        def save_settings():
            peers = []
            for part in peers_edit.text().replace(";", ",").split(","):
                mac = normalize_mac(part.strip())
                if mac and mac not in peers:
                    peers.append(mac)
            if enabled_check.isChecked() and not peers:
                QMessageBox.warning(
                    self.window, "Network Sync",
                    "Add at least one peer MAC address, or untick the sync checkbox.",
                )
                return
            try:
                port = int(port_edit.text().strip() or str(DEFAULT_SYNC_PORT))
                if not (1 <= port <= 65535):
                    raise ValueError
            except ValueError:
                QMessageBox.warning(
                    self.window, "Network Sync",
                    f"The port must be a number between 1 and 65535 (default {DEFAULT_SYNC_PORT}).",
                )
                return
            set_app_setting("sync_enabled", str(bool(enabled_check.isChecked())))
            set_app_setting("sync_peers", ", ".join(peers))
            set_app_setting("sync_port", str(port))
            self._stop_sync_engine()
            self._start_sync_engine()
            refresh_status()

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save & Apply", dialog)
        save_btn.clicked.connect(save_settings)
        buttons.addWidget(save_btn)
        close_btn = QPushButton("Close", dialog)
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        dialog.finished.connect(lambda _=None: setattr(self, "_sync_dialog_refresh", None))
        dialog.exec()

    
    def _start_metadata_fetcher(self):
        """Start background thread to fetch metadata and remove long videos"""
        # Retire any previous fetcher WITHOUT waiting for it.  The old stop()
        # blocked the UI thread until the in-flight yt-dlp request finished,
        # which froze the app mid-search on slow networks.
        self._retired_fetchers = [f for f in self._retired_fetchers if f.isRunning()]
        if self.metadata_fetcher and self.metadata_fetcher.isRunning():
            old = self.metadata_fetcher
            old.request_stop()  # non-blocking: just flags the thread
            self._retired_fetchers.append(old)

        self.metadata_fetcher = MetadataFetcher(self.current_results, max_duration=600)
        # Bind the fetcher to the current search generation: if the user
        # switches views before it finishes, its late arrivals are dropped
        # instead of mutating whatever list happens to be on screen.
        generation = self._search_generation
        self.metadata_fetcher.video_too_long.connect(
            lambda url, g=generation: self._on_video_too_long(g, url)
        )
        self.metadata_fetcher.metadata_ready.connect(
            lambda url, metadata, g=generation: self._on_metadata_ready(g, url, metadata)
        )
        self.metadata_fetcher.start()

    def _on_metadata_ready(self, generation, url, metadata):
        """Enrich search result dicts with fetched duration/artist/thumbnail."""
        if generation != self._search_generation:
            # The view changed since this fetcher started; don't touch it.
            return
        for result in self.current_results:
            if result.get("url") != url:
                continue
            if metadata.get("duration"):
                result["duration"] = metadata["duration"]
            if metadata.get("artist"):
                result["artist"] = metadata["artist"]
            if metadata.get("thumbnail"):
                result["thumbnail"] = metadata["thumbnail"]
            # Refresh only this row in place.  Rebuilding the whole list on
            # every metadata arrival made rows flicker and broke clicking
            # during the ~10s the background fetcher runs after a search.
            self.window.queue_panel.update_item_data(url, result)
            break

    def _update_now_playing(self):
        """Update the UI with the currently playing track title and artwork."""
        current_item = self._playing_source()
        if current_item is None:
            # Nothing has actually been played yet — the queue's anchor must
            # not pose as "now playing".
            self._now_playing_item = None
            self.window.cover_widget.set_track_info("No track selected")
            self.window.cover_widget.clear()
            self.window.set_track_info("No track selected")
            self.window.fullscreen_player.set_track_info("No track selected", "")
            self.window.fullscreen_player.set_cover_art(QPixmap())
            if self.mpris:
                self.mpris.clear_track()
                self._push_mpris_capabilities()
            return

        queue_sources = []
        if getattr(self.window.queue_panel, "items_data", None):
            queue_sources.extend(self.window.queue_panel.items_data)
        if getattr(self.window.queue_panel, "master_items", None):
            queue_sources.extend(self.window.queue_panel.master_items)
        queue_sources.extend(self.current_results)

        matching_track = next(
            (
                track for track in queue_sources
                if isinstance(track, dict)
                and current_item in (track.get("file_path"), track.get("url"))
            ),
            None,
        )
        if matching_track is not None:
            # Remember the info keyed by source: browsing to a view that does
            # not contain the playing song keeps showing the real title and
            # artwork instead of jumping to whatever the new view anchors on.
            self._now_playing_item = (current_item, matching_track)
        else:
            cached = self._now_playing_item
            matching_track = cached[1] if cached and cached[0] == current_item else None

        if matching_track is not None:
            title, artist, artwork = self._track_display_data(matching_track)
        else:
            title = current_item.split("/")[-1] if isinstance(current_item, str) else "Unknown track"
            artist = "Unknown Artist"
            artwork = None

        self.window.cover_widget.set_track_info(title or "Unknown track", artist or "Unknown Artist")
        self.window.fullscreen_player.set_track_info(title or "Unknown track", artist or "Unknown Artist")
        self._set_cover_art(artwork)
        self.window.set_track_info(title or "Unknown track")
        if self.mpris:
            self.mpris.set_track(
                title or "Unknown track",
                artist or "Unknown Artist",
                artwork,
                length_ms=self.player.get_duration(),
            )
            self._push_mpris_capabilities()

    def _track_display_data(self, item):
        """Resolve title, artist, and artwork for a queue item."""
        file_path = item.get("file_path")
        title = item.get("title") or file_path or item.get("url") or "Unknown track"
        artist = item.get("artist") or "Unknown Artist"
        artwork = item.get("thumbnail")

        if file_path:
            metadata = get_track_metadata(file_path)
            title = metadata.get("title") or title
            artist = metadata.get("artist") or artist
            sidecar = thumbnail_path_for_audio(file_path)
            artwork = sidecar if Path(sidecar).exists() else (metadata.get("thumbnail") or artwork)

        return title, artist, artwork

    def _set_cover_art(self, artwork, include_fullscreen=True):
        """Load local artwork immediately; fetch remote art asynchronously so
        the UI thread never blocks on a slow thumbnail download."""
        self._cover_token += 1
        token = self._cover_token

        if not artwork:
            self._apply_cover_art(None, token, include_fullscreen)
            return

        if isinstance(artwork, str) and Path(artwork).exists():
            self._apply_cover_art(QPixmap(artwork), token, include_fullscreen)
            return

        cached = ThumbnailLoader.cached_pixmap(artwork)
        if cached is not None:
            self._apply_cover_art(cached, token, include_fullscreen)
            return

        ThumbnailLoader.instance().fetch_async(
            artwork,
            lambda pix, t=token, fs=include_fullscreen: self._apply_cover_art(pix, t, fs),
        )

    def _apply_cover_art(self, pixmap, token, include_fullscreen=True):
        """Apply cover art unless a newer selection superseded it meanwhile."""
        if token != self._cover_token:
            return
        if pixmap is None or pixmap.isNull():
            self.window.cover_widget.clear_cover_art()
            if include_fullscreen:
                self.window.fullscreen_player.set_cover_art(QPixmap())
        else:
            self.window.cover_widget.set_cover_art(pixmap)
            if include_fullscreen:
                self.window.fullscreen_player.set_cover_art(pixmap)
    
    def _on_video_too_long(self, generation, url):
        """Remove a video from results if it's too long"""
        if generation != self._search_generation:
            # The view changed since this fetcher started; don't touch it.
            return
        # Remove from internal list
        self.current_results = [r for r in self.current_results if r.get("url") != url]
        # Keep the queue panel's master list in sync so the removed video
        # doesn't reappear when the panel is next rebuilt.
        panel = self.window.queue_panel
        if panel.master_items:
            panel.master_items = [i for i in panel.master_items if i.get("url") != url]
        # Remove from UI (single surgical row removal, no list rebuild)
        panel.remove_item_by_url(url)

    def _restore_last_view(self):
        last_view = get_app_setting("last_view", "playlists")
        last_playlist_id = get_app_setting("last_playlist_id")

        if last_view == "playlist" and last_playlist_id:
            try:
                self.handle_open_playlist(int(last_playlist_id))
                return
            except ValueError:
                if last_playlist_id == "all":
                    self.handle_open_playlist("all")
                    return

        self.handle_load_playlists()

    def _sync_queue_from_current_items(self):
        track_items = [item for item in self.current_results if item.get("type") != "playlist"]
        if not track_items:
            self.active_queue_urls = []
            self.window.queue_panel.add_items(self.current_results, preserve_order=False, current_item_source=self._playing_source())
            return

        urls = [item.get("file_path") or item.get("url") for item in track_items if item.get("file_path") or item.get("url")]
        if not urls:
            self.active_queue_urls = []
            self.window.queue_panel.add_items(self.current_results, preserve_order=False, current_item_source=self._playing_source())
            return

        current_item = self.player._current_item
        if current_item not in urls:
            current_item = urls[0]

        self.active_queue_urls = list(urls)
        self.player.set_queue(urls, current_item=current_item)
        self._refresh_queue_display(reset_scroll=True)

    def _refresh_queue_display(self, reset_scroll=False):
        track_items = [item for item in self.current_results if item.get("type") != "playlist"]
        if not track_items:
            self.window.queue_panel.add_items(
                self.current_results, preserve_order=False, current_item_source=self._playing_source()
            )
            return

        # If the player queue is populated, show items in that playback order.
        ordered_sources = list(self.player.queue) if self.player.queue else self.active_queue_urls
        queued_next = self.player._next_sources[0] if self.player._next_sources else None

        if ordered_sources:
            self.window.queue_panel.master_items = self.current_results
            self.window.queue_panel.set_playback_order(
                ordered_sources,
                current_source=self._playing_source(),
                queued_next=queued_next,
                reset_scroll=reset_scroll,
            )
            # When the view is first loaded (startup / playlist switch) the sort
            # widget may have re-ordered the display.  Make sure the player's queue
            # matches the visual order so Next / Prev follow what is on-screen.
            if reset_scroll:
                visual_urls = [
                    i.get("file_path") or i.get("url")
                    for i in self.window.queue_panel.items_data
                    if i.get("file_path") or i.get("url")
                ]
                if visual_urls:
                    self.handle_playback_order_changed(visual_urls)
        else:
            self.window.display_results(
                track_items, preserve_order=True, current_item_source=self._playing_source()
            )

    def handle_playback_order_changed(self, urls):
        """Called when the user's sort choice changes the visible order.

        Keep the player queue in sync so Next / Prev / autoplay follow
        what is shown on-screen.
        """
        if not urls:
            return
        self.active_queue_urls = list(urls)
        self.player.set_queue(urls, current_item=self.player._current_item)
        self._update_now_playing()

    def _current_visual_order(self, fallback=None):
        """Return song sources in the exact order shown in the queue panel.

        current_results stays in original database order, so rebuilding the
        playback queue from it would silently discard the user's active sort
        and its direction. The queue panel's items_data always mirrors the
        visible, sorted order, so prefer that.
        """
        sources = [
            item.get("file_path") or item.get("url")
            for item in getattr(self.window.queue_panel, "items_data", [])
        ]
        sources = [source for source in sources if source]
        if sources:
            return sources
        if fallback:
            return list(fallback)
        return []

    def _refresh_current_view_for_playlists(self, playlist_ids):
        """Refresh the open view after new songs landed in the given playlists.

        Called whenever downloads finish (add-to-playlist, Spotify sync) so
        newly downloaded songs appear in the visible queue without a manual
        reopen. Views the new songs do not belong to (a different playlist,
        a search) are left untouched. Playback is never interrupted: the
        current track stays anchored as the queue is rebuilt around it.
        """
        if not playlist_ids:
            return
        if self.current_playlist_id == "all":
            # The All Songs view contains every playlist's songs.
            self.handle_open_playlist("all")
        elif self.current_playlist_id in playlist_ids:
            self.handle_open_playlist(self.current_playlist_id)
        elif self.current_playlist_id is None and any(
            item.get("type") == "playlist" for item in self.current_results
        ):
            # Browsing the playlist list: refresh so the song counts update.
            self.handle_load_playlists()

    def handle_open_playlist(self, playlist_id):
        # Any in-flight search is stale now; don't let it replace this view.
        self._search_generation += 1
        QApplication.restoreOverrideCursor()
        self.current_playlist_id = playlist_id
        # Clear search inputs when opening a playlist (fresh context)
        self.window.search_panel.clear()
        self.window.queue_panel.filter_input.clear()
        # Only force sort to "Shuffled" when shuffle mode itself is on.
        # Otherwise preserve the user's last sort preference.
        if self.player.shuffle_enabled:
            self.window.queue_panel.set_sort_mode("Shuffled", reverse=True)
        if playlist_id == "all":
            playlist_songs = get_all_playlist_songs_flat()
        else:
            playlist_songs = get_playlist_songs(playlist_id)
        self.current_results = [
            self._enrich_track_dict({"title": title, "url": url, "file_path": file_path, "artist": artist, "added_at": added_at, "type": "track"})
            for title, url, file_path, artist, added_at in playlist_songs
        ]
        self.active_queue_urls = [
            track.get("file_path") or track.get("url")
            for track in self.current_results
            if track.get("file_path") or track.get("url")
        ]
        current_item = self.player._current_item
        if current_item not in self.active_queue_urls:
            current_item = self.active_queue_urls[0] if self.active_queue_urls else None
        self.player.set_queue(self.active_queue_urls, current_item=current_item)
        self._refresh_queue_display(reset_scroll=True)
        set_app_setting("last_view", "playlist")
        set_app_setting("last_playlist_id", str(playlist_id))
        print(f"Opened playlist {playlist_id} with {len(playlist_songs)} songs")

    def _enrich_track_dict(self, item):
        """Add per-file thumbnail/artist from db into an item dict."""
        file_path = item.get("file_path")
        if file_path:
            metadata = get_track_metadata(file_path)
            if metadata:
                item.setdefault("artist", metadata.get("artist"))
                item.setdefault("thumbnail", metadata.get("thumbnail"))
            sidecar = thumbnail_path_for_audio(file_path)
            if Path(sidecar).exists():
                item["thumbnail"] = sidecar
        return item

    def handle_remove_song(self, item):
        """Confirm and remove a local track from the open playlist.

        From the All Songs view the song is removed from every playlist,
        since that view spans all of them.
        """
        if self.current_playlist_id is None or not item or not item.get("file_path"):
            return

        from_all_view = self.current_playlist_id == "all"
        title = item.get("title") or Path(item["file_path"]).stem
        confirmation = QMessageBox(self.window)
        confirmation.setIcon(QMessageBox.Warning)
        confirmation.setWindowTitle("Remove Song")
        if from_all_view:
            confirmation.setText(f'Remove "{title}" from ALL playlists and delete its file?')
        else:
            confirmation.setText(f'Remove "{title}" from this playlist and delete its file?')
        confirmation.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirmation.setDefaultButton(QMessageBox.Yes)
        confirmation.setEscapeButton(QMessageBox.No)
        if confirmation.exec() != QMessageBox.Yes:
            return

        source = item["file_path"]
        if source == self.player._playing_item:
            # The removed song is the one actually playing (the queue anchor
            # may point at another playlist's song while browsing around).
            self.player.stop()
            self.player._playing_item = None
            self.player._has_media = False

        if from_all_view:
            removed = remove_song_everywhere(source)
        else:
            removed = remove_playlist_song(self.current_playlist_id, source)
        if not removed:
            QMessageBox.warning(self.window, "Remove Song", "The song could not be removed.")
            return

        self.current_results = [
            track for track in self.current_results
            if track.get("file_path") != source
        ]
        remaining_urls = [
            track.get("file_path") or track.get("url")
            for track in self.current_results
            if track.get("file_path") or track.get("url")
        ]
        # Rebuild the queue in the order currently shown on-screen so the
        # active sort and its direction survive deleting a song.
        # current_results is in database order; rebuilding from it silently
        # reversed the play order whenever the view was sorted descending.
        ordered_remaining = [
            url
            for url in self._current_visual_order(fallback=remaining_urls)
            if url and url != source
        ]
        if not ordered_remaining:
            ordered_remaining = remaining_urls
        self.active_queue_urls = ordered_remaining
        current_item = self.player._current_item
        if current_item not in ordered_remaining:
            current_item = ordered_remaining[0] if ordered_remaining else None
        self.player.set_queue(ordered_remaining, current_item=current_item)
        self._refresh_queue_display()
        self._update_now_playing()

    def handle_rename_item(self, item):
        """Rename a playlist or a song and refresh the current view."""
        if not item:
            return

        if item.get("type") == "playlist" and item.get("playlist_id") == "all":
            QMessageBox.information(self.window, "Rename", "The All Songs playlist cannot be renamed.")
            return

        old_title = item.get("title") or ""
        label = "Playlist name:" if item.get("type") == "playlist" else "Song title:"
        new_title, ok = QInputDialog.getText(
            self.window,
            "Rename Playlist" if item.get("type") == "playlist" else "Rename Song",
            label,
            text=old_title,
        )
        new_title = new_title.strip()
        if not ok or not new_title or new_title == old_title:
            return

        try:
            if item.get("type") == "playlist":
                if rename_playlist(item.get("playlist_id"), new_title) is None:
                    raise ValueError("A playlist with that name may already exist.")
                self.handle_load_playlists()
                return

            if self.current_playlist_id is None or not item.get("file_path"):
                return
            old_path = item["file_path"]
            if self.current_playlist_id == "all":
                # The All Songs view spans every playlist: rename the song
                # in all of them, not just one.
                new_path = rename_song_globally(old_path, new_title)
            else:
                new_path = rename_playlist_song(self.current_playlist_id, old_path, new_title)
            if not new_path:
                raise ValueError("The song file could not be renamed.")
            for track in self.current_results:
                if track.get("file_path") == old_path:
                    track["title"] = new_title
                    track["file_path"] = new_path
            # Keep the queue in the order currently shown on-screen (the
            # user's active sort), not database order.
            self.active_queue_urls = [
                new_path if source == old_path else source
                for source in self._current_visual_order(fallback=self.active_queue_urls)
            ]
            if self.player._playing_item == old_path:
                # The renamed file is the one actually playing — keep the
                # "now playing" identity pointed at its new path.
                self.player._playing_item = new_path
            current_item = new_path if self.player._current_item == old_path else self.player._current_item
            self.player.set_queue(self.active_queue_urls, current_item=current_item)
            self._refresh_queue_display()
            self._update_now_playing()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.window, "Rename", str(error))

    def handle_delete_playlist(self, item):
        """Require three confirmations before deleting a playlist."""
        if not item or item.get("type") != "playlist":
            return

        if item.get("playlist_id") == "all":
            QMessageBox.information(self.window, "Delete Playlist", "The All Songs playlist cannot be deleted.")
            return

        playlist_name = item.get("title") or "this playlist"
        prompts = [
            f'Delete the playlist "{playlist_name}"?',
            "Its local folder and all downloaded songs will also be removed.",
        ]
        for prompt in prompts:
            if QMessageBox.question(
                self.window,
                "Confirm Playlist Deletion",
                prompt,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            ) != QMessageBox.Yes:
                return

        final_box = QMessageBox(self.window)
        final_box.setIcon(QMessageBox.Warning)
        final_box.setWindowTitle("Final Confirmation")
        final_box.setText(f'Final confirmation: delete "{playlist_name}"?')
        trash_button = final_box.addButton("Move to Trash", QMessageBox.AcceptRole)
        delete_button = final_box.addButton("Delete Permanently", QMessageBox.DestructiveRole)
        final_box.addButton("Cancel", QMessageBox.RejectRole)
        final_box.exec()
        clicked = final_box.clickedButton()
        if clicked not in (trash_button, delete_button):
            return

        use_trash = clicked is trash_button
        if not delete_playlist(item.get("playlist_id"), use_trash=use_trash):
            QMessageBox.warning(self.window, "Delete Playlist", "The playlist could not be deleted.")
            return

        if self.current_playlist_id == item.get("playlist_id"):
            self.current_playlist_id = None
            self.active_queue_urls = []
            self.player.set_queue([])
        self.handle_load_playlists()



if __name__ == "__main__":
    init_db()
    
    app = QApplication(sys.argv)
    
    # Create main window with new modular UI
    window = MainWindow()
    
    # Create controller to handle business logic
    controller = MusicAppController(window)
    
    # Load stylesheet relative to this script's directory
    from pathlib import Path
    qss_path = Path(__file__).resolve().parent / "ui" / "styles.qss"
    if qss_path.exists():
        with qss_path.open("r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    else:
        print(f"Warning: styles.qss not found at {qss_path}")
    
    # Size the window for a 1920x1080 design target.
    # Maximize only when the screen is exactly 1080p; otherwise
    # open as a centered 1920x1080 window (client area).
    screen = app.primaryScreen()
    if screen:
        geo = screen.availableGeometry()
        if geo.width() == 1920 and geo.height() == 1080:
            window.showMaximized()
        elif geo.width() >= 1920 and geo.height() >= 1080:
            window.resize(1920, 1080)
            window.move(
                geo.x() + (geo.width() - 1920) // 2,
                geo.y() + (geo.height() - 1080) // 2,
            )
            window.show()
        else:
            window.show()
    else:
        window.show()

    # In touch-panel mode the seek bar relies on the cover widget having
    # real geometry; re-sync once after the window is fully laid out.
    if window._touch_mode:
        QTimer.singleShot(100, window._sync_seek_row)

    # Check linked Spotify playlists once for songs added since the last run.
    controller.start_spotify_sync()

    app.aboutToQuit.connect(controller.shutdown)

    if sys.platform == "win32":
        media_filter = install_media_hotkeys(app, window)
        if media_filter is None:
            print("Global media hotkeys are unavailable on this platform or missing dependencies.")
    else:
        # Linux: register an MPRIS service so the desktop's media keys,
        # media widgets, and OSD control the player even when unfocused.
        controller.install_mpris()

    sys.exit(app.exec())