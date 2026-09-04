"""Import a Spotify playlist into the Music Engine library."""

import os
from datetime import datetime

import spotipy
from PySide6.QtCore import QThread, Signal
from spotipy.oauth2 import SpotifyClientCredentials
from yt_dlp import YoutubeDL

from db import (
    FFMPEG_LOCATION,
    add_downloaded_song_to_playlist,
    create_playlist,
    download_audio_to_folder,
)

# These values are portable across operating systems. Keep them in this small
# configuration section so the importer does not depend on shell variables.
SPOTIFY_CLIENT_ID = "bdb2e21d2b384b31bea7e4c622140d81"
SPOTIFY_CLIENT_SECRET = "9fca954348d84b658aeb2987a068ef69"
PLAYLIST_NAME = "Spotify Import"
MAX_VIDEO_DURATION = 10 * 60
SEARCH_RESULTS = 10


def _playlist_id(url):
    return url.split("playlist/", 1)[1].split("?", 1)[0] if "playlist/" in url else url


def _spotify_client():
    return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
    ))


def get_best_youtube_result(track_name, artist_name):
    """Return the first short, non-live YouTube result with full metadata."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"ytsearch{SEARCH_RESULTS}:{track_name} - {artist_name}",
                download=False,
            )
    except Exception:
        return None

    for entry in info.get("entries") or []:
        duration = entry.get("duration")
        if entry.get("id") and duration and duration <= MAX_VIDEO_DURATION and not entry.get("is_live"):
            return {
                "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry['id']}",
                "thumbnail": entry.get("thumbnail"),
            }
    return None


def _tracks(client, playlist_url):
    results = client.playlist_tracks(_playlist_id(playlist_url))
    tracks = list(results.get("items") or [])
    while results.get("next"):
        results = client.next(results)
        tracks.extend(results.get("items") or [])
    return tracks


def import_playlist(playlist_url, progress=None):
    """Download a Spotify playlist and return (playlist_id, failures, name)."""
    playlist_id = _playlist_id(playlist_url)
    client = _spotify_client()
    playlist = client.playlist(playlist_id, fields="name")
    playlist_row = create_playlist(playlist.get("name") or PLAYLIST_NAME)
    if not playlist_row:
        raise RuntimeError("Could not create the import playlist")

    playlist_id, playlist_name, folder = playlist_row
    failures = []
    tracks = _tracks(client, playlist_url)
    for index, item in enumerate(tracks, start=1):
        track = item.get("track") or {}
        name = track.get("name") or "Unknown track"
        artists = track.get("artists") or []
        artist = artists[0].get("name") if artists else "Unknown artist"
        song_name = f"{name} - {artist}"
        if progress:
            progress(index, len(tracks), song_name)

        try:
            candidate = get_best_youtube_result(name, artist)
            if not candidate:
                raise RuntimeError("No short YouTube match found")
            file_path = download_audio_to_folder(candidate["url"], name, folder)
            if not file_path:
                raise RuntimeError("Audio download failed")
            images = track.get("album", {}).get("images") or []
            thumbnail = images[0].get("url") if images else candidate.get("thumbnail")
            add_downloaded_song_to_playlist(
                name, candidate["url"], playlist_id, file_path, artist, thumbnail
            )
        except Exception as error:
            failures.append((song_name, str(error)))
            _log_failure(folder, song_name, str(error))
    return playlist_id, failures, playlist_name


def _log_failure(folder, song, reason):
    timestamp = datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(folder, "((__failed_songs__)).txt"), "a", encoding="utf-8") as log:
        log.write(f"[{timestamp}] {song}: {reason}\n")


class SpotifyImportWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(int, int, str)
    failed = Signal(str)

    def __init__(self, playlist_url, parent=None):
        super().__init__(parent)
        self.playlist_url = playlist_url

    def run(self):
        try:
            playlist_id, failures, playlist_name = import_playlist(
                self.playlist_url,
                lambda current, total, title: self.progress.emit(current, total, title)
            )
            self.completed.emit(playlist_id, len(failures), playlist_name)
        except Exception as error:
            self.failed.emit(str(error))
