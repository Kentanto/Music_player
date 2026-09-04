import sys
import urllib.request
import warnings
from pathlib import Path
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
from PySide6.QtGui import QPixmap

from ui import MainWindow
from player import Player
from media_hotkeys import install_media_hotkeys
from search import search_youtube
from db import (
    init_db,
    create_playlist,
    get_playlists,
    add_song_to_playlist,
    get_playlist_songs,
    remove_playlist_song,
    get_app_setting,
    set_app_setting,
    backfill_thumbnails,
    thumbnail_path_for_audio,
    get_track_metadata,
)
from metadata_fetcher import MetadataFetcher
from spotify_download import SpotifyImportWorker

# Suppress all warnings
warnings.filterwarnings("ignore")


class MusicAppController:
    """Business logic controller - bridges UI and backend"""
    
    def __init__(self, window):
        self.window = window
        self.player = Player()
        self.current_results = []
        self.metadata_fetcher = None  # Background thread for duration checking
        self.thumbnail_backfill = None
        self.spotify_import_worker = None
        self.active_queue_urls = []
        self.current_playlist_id = None
        
        # Connect player signals to update UI
        self.player.signals.position_changed.connect(self.on_player_position_changed)
        self.player.signals.duration_changed.connect(self.on_player_duration_changed)
        self.player.signals.state_changed.connect(self.on_player_state_changed)
        self.player.signals.autoplay_next.connect(self.handle_next_autoplay)
        
        # Connect all UI signals to handlers
        self.connect_handlers()

        saved_shuffle = get_app_setting("shuffle_enabled", "False")
        self.player.shuffle_enabled = saved_shuffle.lower() == "true"
        self.window.player_bar.set_shuffle_state(self.player.shuffle_enabled)
        self.window.queue_panel.sort_combo.setCurrentText(
            "Shuffled" if self.player.shuffle_enabled else "Date Added"
        )
        
        # Restore the last playlist or show playlists first
        self._restore_last_view()
        
        # Set initial volume AFTER handlers are connected
        self.window.player_bar.volume_slider.setValue(30)

        # Fill artwork for tracks downloaded before thumbnail support existed.
        self._start_thumbnail_backfill()
    
    def on_player_position_changed(self, position_ms):
        """Update seek bar as song plays"""
        duration_ms = self.player.get_duration()
        if duration_ms > 0:
            position_fraction = position_ms / duration_ms
            self.window.update_player_time(position_ms / 1000, duration_ms / 1000)
            self.window.player_bar.set_seek_position(position_fraction)
    
    def on_player_duration_changed(self, duration_ms):
        """Update total duration display"""
        self.window.update_player_time(0, duration_ms / 1000)
    
    def on_player_state_changed(self, state):
        """Update UI button state when playback changes."""
        is_playing = state == self.player.player.PlaybackState.PlayingState
        self.window.player_bar.set_play_pause_state(is_playing)
    
    def connect_handlers(self):
        """Connect UI signals to business logic"""
        self.window.search_requested.connect(self.handle_search)
        self.window.play_pause_track.connect(self.handle_play_pause)
        self.window.play_track_index.connect(self.handle_play_item)
        self.window.queue_next_requested.connect(self.handle_queue_next)
        self.window.queue_panel.remove_requested.connect(self.handle_remove_song)
        self.window.next_track.connect(self.handle_next)
        self.window.prev_track.connect(self.handle_prev)
        self.window.shuffle_toggled.connect(self.handle_shuffle_toggle)
        self.window.load_playlists.connect(self.handle_load_playlists)
        self.window.add_to_playlist.connect(self.handle_add_to_playlist)
        self.window.import_list_requested.connect(self.handle_import_list)
        self.window.open_playlist_requested.connect(self.handle_open_playlist)
        self.window.volume_changed.connect(self.handle_volume_change)
        self.window.seek_requested.connect(self.handle_seek)
    
    def handle_search(self, query):
        """Search YouTube for songs"""
        print(f"Searching: {query}")
        self.current_results = search_youtube(query)
        
        # Start background metadata fetcher to remove long videos
        if self.current_results:
            self._start_metadata_fetcher()
        self._sync_queue_from_current_items()

    def _start_thumbnail_backfill(self):
        """Backfill artwork without blocking the application window."""
        from PySide6.QtCore import QThread

        class ThumbnailBackfill(QThread):
            def run(self):
                print("[thumbnail-backfill] Started", flush=True)
                backfill_thumbnails()

        self.thumbnail_backfill = ThumbnailBackfill(self.window)
        self.thumbnail_backfill.start()
    
    def handle_play(self):
        """Play current track selection or resume if paused"""

        try:
            if self.player.get_duration() > 0:
                self.player.resume()
                self._update_now_playing()
                return

            item = self.window.queue_panel.get_current_item()
            if not item:
                return

            self.handle_play_item(item)

        except Exception:
            item = self.window.queue_panel.get_current_item()
            if item:
                self.handle_play_item(item)
    
    def handle_play_item(self, item):
        """Play a track from a dict item (shuffle-safe, index-free)"""

        if not item or item.get("type") == "playlist":
            return

        visible_urls = self.window.get_current_queue_urls()

        selected_url = self._resolve_url(item)
        if not selected_url:
            return

        if self.player.shuffle_enabled and selected_url in self.player._base_queue:
            urls = list(self.player._base_queue)
        else:
            urls = list(visible_urls)
        self.active_queue_urls = list(urls)
        # ensure item exists in queue
        if selected_url not in urls:
            # fallback: try stable match instead of index guessing
            for u in urls:
                if u == selected_url:
                    break
            else:
                if urls:
                    selected_url = urls[0]

        print("Selected URL:", selected_url)
        print("Queue length:", len(urls))
        print("Found in queue:", selected_url in urls)

        self.player.set_queue(urls, current_item=selected_url)
        self.player.play()

        self._refresh_queue_display()
        self._update_now_playing()
    
    def handle_pause(self):
        self.player.pause()
    
    def handle_resume(self):
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

        actual_queue = list(self.player.queue) or list(self.active_queue_urls) or self.window.get_current_queue_urls()
        if not actual_queue or source == self.player._current_item:
            return

        self.player.queue_next(source)
        self.active_queue_urls = list(self.player.queue)
        self._refresh_queue_display()
    
    def handle_next_autoplay(self):
        """Auto-play next track when current finishes"""
        if self.player.index + 1 < len(self.player.queue):
            self.handle_next()
            print("Auto-playing next track")
        else:
            print("End of queue reached")
    
    def handle_prev(self):
        self.player.previous()
        self._refresh_queue_display()
        self._update_now_playing()

    def _resolve_url(self, item):
        if not item:
            return None
        return item.get("file_path") or item.get("url")

    def handle_shuffle_toggle(self, enabled):
        self.window.queue_panel.sort_combo.setCurrentText(
            "Shuffled" if enabled else "Date Added"
        )
        queue_urls = self.active_queue_urls or self.window.get_current_queue_urls() or self.player._base_queue
        if not queue_urls:
            self.player.shuffle_enabled = False
            self.player._ordered_queue = list(self.player._base_queue)
            self.player.queue = list(self.player._base_queue)
            self.window.player_bar.set_shuffle_state(False)
            self.window.queue_panel.sort_combo.setCurrentText("Date Added")
            set_app_setting("shuffle_enabled", "False")
            return

        current_item = self.player._current_item

        if current_item not in queue_urls:
            current_index = self.window.get_current_track_index()
            if 0 <= current_index < len(queue_urls):
                current_item = queue_urls[current_index]
            else:
                current_item = queue_urls[0]

        self.player.set_queue(queue_urls, current_item=current_item)
        self.player.toggle_shuffle(enabled)
        self.window.player_bar.set_shuffle_state(self.player.shuffle_enabled)
        set_app_setting("shuffle_enabled", str(bool(self.player.shuffle_enabled)))
        self.active_queue_urls = list(queue_urls)
        self._refresh_queue_display()
    
    def handle_load_playlists(self):
        """Load playlist list for browsing"""
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
        self.current_results = display_data
        self._refresh_queue_display()
        set_app_setting("last_view", "playlists")
        print(f"Loaded {len(playlists)} playlists")

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

        self.spotify_import_worker = SpotifyImportWorker(playlist_url, self.window)
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

    def _on_import_completed(self, playlist_id, failed_count, playlist_name):
        self.handle_open_playlist(playlist_id)
        message = f'Imported "{playlist_name}".'
        if failed_count:
            message += f" {failed_count} track(s) were logged as failed."
        QMessageBox.information(self.window, "Import List", message)

    def _on_import_failed(self, reason):
        QMessageBox.warning(self.window, "Import List", f"Import failed: {reason}")


    def handle_add_to_playlist(self):
        """Add selected song to an existing or new playlist."""
        index = self.window.get_current_track_index()
        if index < 0 or index >= len(self.current_results):
            return

        item = self.current_results[index]
        if item.get("type") == "playlist":
            QMessageBox.information(self.window, "Add to Playlist", "Please select a track to add to a playlist.")
            return

        track_title = item.get("title")
        track_url = item.get("url")
        playlist_id = self._choose_playlist()
        if not playlist_id:
            return

        saved_path = add_song_to_playlist(track_title, track_url, playlist_id)
        if saved_path:
            QMessageBox.information(self.window, "Playlist", f"Added \"{track_title}\" to playlist successfully.\n\nFile: {saved_path}")
        else:
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
        # print(f"Volume changed to: {value}")
        self.player.set_volume(value)
    
    def handle_seek(self, position):
        """Seek to position (0.0-1.0)"""
        self.player.seek(position)

    def shutdown(self):
        """Stop background work before Qt destroys the application."""
        if self.metadata_fetcher and self.metadata_fetcher.isRunning():
            self.metadata_fetcher.stop()
        if self.thumbnail_backfill and self.thumbnail_backfill.isRunning():
            self.thumbnail_backfill.quit()
            self.thumbnail_backfill.wait()
        if self.spotify_import_worker and self.spotify_import_worker.isRunning():
            self.spotify_import_worker.quit()
            self.spotify_import_worker.wait()
        self.player.stop()
    
    def _start_metadata_fetcher(self):
        """Start background thread to fetch metadata and remove long videos"""
        # Stop any existing fetcher
        if self.metadata_fetcher:
            self.metadata_fetcher.stop()
        
        # Create new fetcher
        self.metadata_fetcher = MetadataFetcher(self.current_results, max_duration=600)
        self.metadata_fetcher.video_too_long.connect(self._on_video_too_long)
        self.metadata_fetcher.start()

    def _update_now_playing(self):
        """Update the UI with the currently playing track title and artwork."""
        current_item = self.player._current_item
        if current_item is None:
            self.window.cover_widget.set_track_info("No track selected")
            self.window.cover_widget.clear()
            self.window.player_bar.set_track_info("No track selected")
            return

        title = None
        artist = None
        artwork = None

        queue_sources = []
        if getattr(self.window.queue_panel, "items_data", None):
            queue_sources.extend(self.window.queue_panel.items_data)
        if getattr(self.window.queue_panel, "master_items", None):
            queue_sources.extend(self.window.queue_panel.master_items)
        queue_sources.extend(self.current_results)

        for track in queue_sources:
            if not isinstance(track, dict):
                continue
            if track.get("file_path") and current_item == track.get("file_path"):
                title = track.get("title") or track.get("file_path") or "Unknown track"
                metadata = get_track_metadata(track["file_path"])
                title = metadata.get("title") or title
                artist = metadata.get("artist")
                artwork = (
                    thumbnail_path_for_audio(track["file_path"])
                    if Path(thumbnail_path_for_audio(track["file_path"])).exists()
                    else metadata.get("thumbnail")
                )
                break
            if track.get("url") and current_item == track.get("url"):
                title = track.get("title") or track.get("file_path") or "Unknown track"
                artist = track.get("artist")
                artwork = track.get("thumbnail")
                break

        if title is None and isinstance(current_item, str):
            title = current_item.split("/")[-1] if current_item else "Unknown track"

        self.window.cover_widget.set_track_info(title or "Unknown track", artist or "Unknown Artist")
        self._set_cover_art(artwork)
        self.window.player_bar.set_track_info(title or "Unknown track")

    def _set_cover_art(self, artwork):
        """Load local artwork or a search thumbnail into the cover panel."""
        if not artwork:
            self.window.cover_widget.clear_cover_art()
            return

        if isinstance(artwork, str) and Path(artwork).exists():
            pixmap = QPixmap(artwork)
        else:
            try:
                with urllib.request.urlopen(artwork, timeout=10) as response:
                    pixmap = QPixmap()
                    pixmap.loadFromData(response.read())
            except Exception:
                pixmap = QPixmap()

        if pixmap.isNull():
            self.window.cover_widget.clear_cover_art()
        else:
            self.window.cover_widget.set_cover_art(pixmap)
    
    def _on_video_too_long(self, url):
        """Remove a video from results if it's too long"""
        # Remove from internal list
        self.current_results = [r for r in self.current_results if r.get("url") != url]
        # Remove from UI
        self.window.queue_panel.remove_item_by_url(url)

    def _restore_last_view(self):
        last_view = get_app_setting("last_view", "playlists")
        last_playlist_id = get_app_setting("last_playlist_id")

        if last_view == "playlist" and last_playlist_id:
            try:
                self.handle_open_playlist(int(last_playlist_id))
                return
            except ValueError:
                pass

        self.handle_load_playlists()

    def _sync_queue_from_current_items(self):
        track_items = [item for item in self.current_results if item.get("type") != "playlist"]
        if not track_items:
            self.active_queue_urls = []
            self.window.queue_panel.add_items(self.current_results, preserve_order=False, current_item_source=self.player._current_item)
            return

        urls = [item.get("file_path") or item.get("url") for item in track_items if item.get("file_path") or item.get("url")]
        if not urls:
            self.active_queue_urls = []
            self.window.queue_panel.add_items(self.current_results, preserve_order=False, current_item_source=self.player._current_item)
            return

        current_item = self.player._current_item
        if current_item not in urls:
            current_item = urls[0]

        self.active_queue_urls = list(urls)
        self.player.set_queue(urls, current_item=current_item)
        self._refresh_queue_display()

    def _refresh_queue_display(self):
        track_items = [item for item in self.current_results if item.get("type") != "playlist"]
        if not track_items:
            self.window.queue_panel.add_items(self.current_results, preserve_order=False, current_item_source=self.player._current_item)
            return

        current_sort = self.window.queue_panel.sort_combo.currentText()
        preserve_order = current_sort not in {"Shuffled"}
        self.window.queue_panel.shuffle_order = list(getattr(self.player, "_shuffle_order", []) or self.player.queue)
        self.window.queue_panel.current_item_source = self.player._current_item
        self.window.display_results(track_items, preserve_order=preserve_order, current_item_source=self.player._current_item)

    def handle_open_playlist(self, playlist_id):
        self.current_playlist_id = playlist_id
        self.window.queue_panel.sort_combo.setCurrentText(
            "Shuffled" if self.player.shuffle_enabled else "Date Added"
        )
        playlist_songs = get_playlist_songs(playlist_id)
        self.current_results = [
            {"title": title, "url": url, "file_path": file_path, "type": "track"}
            for title, url, file_path in playlist_songs
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
        self._refresh_queue_display()
        set_app_setting("last_view", "playlist")
        set_app_setting("last_playlist_id", str(playlist_id))
        print(f"Opened playlist {playlist_id} with {len(playlist_songs)} songs")

    def handle_remove_song(self, item):
        """Confirm and remove a local track from the open playlist."""
        if self.current_playlist_id is None or not item or not item.get("file_path"):
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
        self.active_queue_urls = remaining_urls
        self.player.set_queue(remaining_urls)
        self._refresh_queue_display()
        self._update_now_playing()



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

    media_filter = install_media_hotkeys(app, window)
    if media_filter is None:
        print("Global media hotkeys are unavailable on this platform or missing dependencies.")

    sys.exit(app.exec())