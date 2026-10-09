import sys
import warnings
from pathlib import Path
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QThread, Signal

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
    rename_playlist,
    rename_playlist_song,
    delete_playlist,
    get_app_setting,
    set_app_setting,
    thumbnail_path_for_audio,
    get_track_metadata,
    get_all_playlist_songs_flat,
)
from metadata_fetcher import MetadataFetcher
from spotify_download import SpotifyImportWorker
from cec_remote import CecRemoteListener
from mocute_listener import MocuteListener

# Suppress all warnings
warnings.filterwarnings("ignore")


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
        self.add_song_worker = None
        self.cec_remote = None
        self.gamepad = None
        self.mpris = None  # Linux MPRIS bridge (installed in install_mpris)
        self.active_queue_urls = []
        self.current_playlist_id = None
        
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
        self.window.player_bar.set_shuffle_state(self.player.shuffle_enabled)
        self.window.queue_panel.set_sort_mode(
            "Shuffled" if self.player.shuffle_enabled else "Date Added", reverse=True
        )
        
        # Restore the last playlist or show playlists first
        self._restore_last_view()
        
        # Set initial volume AFTER handlers are connected
        self.window.player_bar.volume_slider.setValue(30)
    
    def on_player_position_changed(self, position_ms):
        """Update seek bar as song plays"""
        if self.mpris:
            self.mpris.set_position_ms(position_ms)
        duration_ms = self.player.get_duration()
        if duration_ms > 0:
            position_fraction = position_ms / duration_ms
            self.window.update_player_time(position_ms / 1000, duration_ms / 1000)
            self.window.player_bar.set_seek_position(position_fraction)
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
        self.window.player_bar.set_play_pause_state(is_playing)
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
        self.window.open_playlist_requested.connect(self.handle_open_playlist)
        self.window.volume_changed.connect(self.handle_volume_change)
        self.window.seek_requested.connect(self.handle_seek)
        self.window.seek_delta_requested.connect(self.handle_seek_delta)
        self.window.fullscreen_requested.connect(self.handle_fullscreen)
        self.window.stop_requested.connect(self.player.stop)
        self.window.volume_mute.connect(self.handle_volume_mute)

        fullscreen = self.window.fullscreen_player
        fullscreen.play_pause_clicked.connect(self.handle_play_pause)
        fullscreen.next_clicked.connect(self.handle_next)
        fullscreen.prev_clicked.connect(self.handle_prev)
        fullscreen.volume_changed.connect(self.handle_volume_change)

        self.cec_remote = CecRemoteListener(self.window)
        self.cec_remote.play_pause.connect(self.handle_play_pause)
        self.cec_remote.next_track.connect(self.handle_next)
        self.cec_remote.previous_track.connect(self.handle_prev)
        self.cec_remote.stop_requested.connect(self.player.stop)
        self.cec_remote.back_requested.connect(self.handle_back)
        self.cec_remote.navigation.connect(self.window.navigate_remote)
        self.cec_remote.select_requested.connect(self.window.activate_remote_target)
        if self.cec_remote.available():
            self.cec_remote.start()

        self.gamepad = MocuteListener(self.window)
        self.gamepad.play_pause.connect(self.handle_play_pause)
        self.gamepad.next_track.connect(self.handle_next)
        self.gamepad.previous_track.connect(self.handle_prev)
        self.gamepad.stop_requested.connect(self.player.stop)
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
            return

        item = self.window.queue_panel.get_current_item()
        if not item:
            return

        self.handle_play_item(item)
    
    def handle_play_item(self, item):
        """Play a track from a dict item (shuffle-safe, index-free)"""
        if not item or item.get("type") == "playlist":
            return

        selected_url = self._resolve_url(item)
        if not selected_url:
            return

        # If the song is already in the active player queue, just seek to it
        # and play WITHOUT rebuilding/touching shuffle order.
        if selected_url in self.player.queue:
            idx = self.player.queue.index(selected_url)
            self.player.index = idx
            self.player._current_item = selected_url
            self.player.play()
            self._refresh_queue_display()
            self._update_now_playing()
            return

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

    def handle_track_selected(self, item):
        """Preview selected track metadata without changing playback.

        Only the main-window cover pane previews the selection — the player
        bar and the fullscreen view stay locked to the track that is
        actually playing, so browsing/clicking never rewrites "now playing".
        """
        if not item or item.get("type") == "playlist":
            return

        title, artist, artwork = self._track_display_data(item)
        self.window.cover_widget.set_track_info(title, artist)
        self._set_cover_art(artwork, include_fullscreen=False)
    
    def handle_pause(self):
        self.player.pause()
    
    def handle_resume(self):
        if self.player.get_duration() <= 0 and self._start_queue_if_idle():
            # Nothing loaded yet (fresh open): start the queue instead of
            # resuming an empty player.
            return
        self.player.resume()
    
    def handle_play_pause(self):
        state = self.player.player.playbackState()

        if state == self.player.player.PlaybackState.PlayingState:
            self.player.pause()
            return

        if self.player.get_duration() > 0:
            self.player.resume()
            self._update_now_playing()
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
        self.player.next()
        self._refresh_queue_display()
        self._update_now_playing()
    
    def handle_queue_next(self, item):
        """Insert the selected item as the next track in the current queue."""
        if not item or item.get("type") == "playlist":
            return

        source = self._resolve_url(item)
        if not source:
            return

        self.player.queue_next(source)
        self.active_queue_urls = list(self.player.queue)
        self._refresh_queue_display()
    
    def handle_next_autoplay(self):
        """Auto-play next track when current finishes."""
        self.player._advance()
        self._refresh_queue_display()
        self._update_now_playing()
    
    def handle_prev(self):
        self.player.previous()
        self._refresh_queue_display()
        self._update_now_playing()

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
        self.player.stop()

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

    def _resolve_url(self, item):
        if not item:
            return None
        return item.get("file_path") or item.get("url")

    def _playing_source(self):
        """Source of the track actually playing (or last played), or None
        while playback has never been started — the queue's anchor song
        must not pose as 'now playing' on a freshly opened playlist."""
        if self.player._has_media:
            return self.player._current_item
        return None

    def handle_shuffle_toggle(self, enabled):
        queue_urls = self.active_queue_urls or self.player._base_queue
        if not queue_urls:
            self.player.shuffle_enabled = False
            self.window.player_bar.set_shuffle_state(False)
            self.window.queue_panel.set_sort_mode("Date Added", reverse=True)
            set_app_setting("shuffle_enabled", "False")
            if self.mpris:
                self.mpris.set_shuffle(False)
            return

        current_item = self.player._current_item
        if current_item not in queue_urls:
            current_index = self.window.get_current_track_index()
            current_item = queue_urls[current_index] if 0 <= current_index < len(queue_urls) else queue_urls[0]

        self.player.set_queue(queue_urls, current_item=current_item)
        self.player.toggle_shuffle(enabled)
        self.window.player_bar.set_shuffle_state(self.player.shuffle_enabled)
        self.window.queue_panel.set_sort_mode(
            "Shuffled" if self.player.shuffle_enabled else "Date Added", reverse=True
        )
        set_app_setting("shuffle_enabled", str(bool(self.player.shuffle_enabled)))
        self.active_queue_urls = list(queue_urls)
        self._refresh_queue_display()
        if self.mpris:
            self.mpris.set_shuffle(self.player.shuffle_enabled)
    
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
        QMessageBox.information(self.window, "Import List", message)

    def _on_import_failed(self, reason):
        QMessageBox.warning(self.window, "Import List", f"Import failed: {reason}")


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
            QMessageBox.information(
                self.window, "Playlist", "Another song is still downloading. Please wait for it to finish."
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
            lambda path, title=track_title: self._on_add_song_finished(title, path)
        )
        self.add_song_worker.add_failed.connect(self._on_add_song_failed)
        self.add_song_worker.start()

    def _on_add_song_finished(self, title, saved_path):
        QApplication.restoreOverrideCursor()
        print(f"[playlist-add] success: {saved_path}", flush=True)
        QMessageBox.information(self.window, "Playlist", f"Added \"{title}\" to playlist successfully.\n\nFile: {saved_path}")

    def _on_add_song_failed(self, reason):
        QApplication.restoreOverrideCursor()
        print(f"[playlist-add] failed: {reason}", flush=True)
        QMessageBox.warning(self.window, "Playlist", "Failed to add song to playlist.")

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
        """Update player volume"""
        self.player.set_volume(value)
        self.window.player_bar.set_volume(value)
        self.window.fullscreen_player.set_volume(value)
        if self.mpris:
            self.mpris.set_volume(max(0, min(int(value), 100)) / 100.0)

    def handle_volume_up(self):
        """Bump volume up by 5."""
        slider = self.window.player_bar.volume_slider
        slider.setValue(min(100, slider.value() + 5))

    def handle_volume_down(self):
        """Bump volume down by 5."""
        slider = self.window.player_bar.volume_slider
        slider.setValue(max(0, slider.value() - 5))

    def handle_seek(self, position):
        """Seek to position (0.0-1.0)"""
        self.player.seek(position)
        if self.mpris:
            self.mpris.emit_seeked(self.player.get_position())

    def handle_seek_delta(self, seconds):
        """Move playback by a fixed number of seconds."""
        duration = self.player.get_duration()
        if duration <= 0:
            return
        position = self.player.get_position() + int(seconds * 1000)
        self.player.seek(max(0.0, min(position / duration, 1.0)))
        if self.mpris:
            self.mpris.emit_seeked(self.player.get_position())

    def handle_fullscreen(self):
        fullscreen = self.window.fullscreen_player
        if fullscreen.isVisible():
            fullscreen.close()
        else:
            fullscreen.showFullScreen()
            fullscreen.raise_()
            fullscreen.activateWindow()
            self._update_now_playing()

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
        if self.cec_remote and self.cec_remote.isRunning():
            self.cec_remote.stop()
        if self.gamepad:
            self.gamepad.stop()
        if self.mpris:
            self.mpris.shutdown()
        self.player.stop()
        # Wait for in-flight background stream resolves so their QThreads
        # are not destroyed while running.
        self.player.shutdown()
    
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
        current_item = self.player._current_item
        if current_item is None or not self.player._has_media:
            # Nothing has actually been played yet — the queue's anchor must
            # not pose as "now playing".
            self.window.cover_widget.set_track_info("No track selected")
            self.window.cover_widget.clear()
            self.window.player_bar.set_track_info("No track selected")
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
            title, artist, artwork = self._track_display_data(matching_track)
        else:
            title = current_item.split("/")[-1] if isinstance(current_item, str) else "Unknown track"
            artist = "Unknown Artist"
            artwork = None

        self.window.cover_widget.set_track_info(title or "Unknown track", artist or "Unknown Artist")
        self.window.fullscreen_player.set_track_info(title or "Unknown track", artist or "Unknown Artist")
        self._set_cover_art(artwork)
        self.window.player_bar.set_track_info(title or "Unknown track")
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
        """Confirm and remove a local track from the open playlist."""
        if self.current_playlist_id is None or self.current_playlist_id == "all" or not item or not item.get("file_path"):
            if self.current_playlist_id == "all":
                QMessageBox.information(self.window, "Remove Song", "Songs cannot be removed from the All Songs view.")
            return

        title = item.get("title") or Path(item["file_path"]).stem
        confirmation = QMessageBox(self.window)
        confirmation.setIcon(QMessageBox.Warning)
        confirmation.setWindowTitle("Remove Song")
        confirmation.setText(f'Remove "{title}" from this playlist and delete its file?')
        confirmation.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        confirmation.setDefaultButton(QMessageBox.Yes)
        confirmation.setEscapeButton(QMessageBox.No)
        if confirmation.exec() != QMessageBox.Yes:
            return

        source = item["file_path"]
        if source == self.player._current_item:
            self.player.stop()

        if not remove_playlist_song(self.current_playlist_id, source):
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
    
    window.show()

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