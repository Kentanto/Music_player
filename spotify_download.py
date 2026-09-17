"""Import a Spotify playlist into the Music Engine library."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

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
MIN_VIDEO_DURATION = 45
MAX_VIDEO_DURATION = 10 * 60
SEARCH_RESULTS = 20
NUM_WORKERS = 3
FAILURE_LOG_NAME = "failed_songs.txt"

# These words usually mean an altered, fake or wrong version; we penalise them HEAVILY.
_CRITICAL_BAD = [
    "karaoke", "instrumental", "backing track", "cover", "live", "react",
    "reaction", "trolling", "review", "tutorial", "how to", "howto",
    "parody", "meme", "memes", "funny", "nightcore", "daycore", "8d audio",
    "8d", " slowed", "sped up", "speed up", "nightcore", "bass boosted",
    "1 hour", "10 hours", "hour version", "loop", "chipmunk", "earrape",
    "high pitch", "pitch shifted", "reverb", "clean", "dirty", "demo",
    "acoustic", "unplugged", "live session", "radio edit", "edit", "remix",
    "mashup", "extended", "bootleg", "flip", "sped", "rewind",
]

# Softer negative signals (still bad, but worth less penalty).
_BAD_KEYWORDS = [
    "version", "vs", "vs.", "not", "orchestra", "tribute",
    "remastered", "remaster", "screwed", "chopped", "hour loop",
    "recording", "no vocals", "off vocal", "midi", "synthesia",
]

_GOOD_KEYWORDS = ["official", "topic", "music video", "lyrics", "audio"]
_OFFICIAL_CHANNELS = ["vevo", "official", "topic"]
_STRONG_OFFICIAL = {"official music video", "official audio", "official video"}


def _playlist_id(url):
    return url.split("playlist/", 1)[1].split("?", 1)[0] if "playlist/" in url else url


def _spotify_client():
    return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
    ))


def _score_result(entry, track_name, artist_name, expected_seconds=None):
    """Score a YouTube result; higher is better."""
    title = (entry.get("title") or "").lower().strip()
    uploader = (entry.get("uploader") or entry.get("channel") or "").lower().strip()
    score = 0

    track_name = track_name.lower().strip()
    artist_name = artist_name.lower().strip()

    # --- Exact / strong matches ---
    if track_name in title:
        score += 20
    if artist_name in title:
        score += 12
    if artist_name in uploader:
        score += 15

    # --- Official channel / uploader bonus ---
    for official in _OFFICIAL_CHANNELS:
        if official in uploader:
            score += 8
            break

    # --- Strong official title phrase bonus ---
    for phrase in _STRONG_OFFICIAL:
        if phrase in title:
            score += 6
            break

    # --- Penalize critical karaoke/parody keywords so they never win ---
    for word in _CRITICAL_BAD:
        if word in title:
            score -= 100

    # --- Penalize other bad keywords ---
    for word in _BAD_KEYWORDS:
        if word in title:
            score -= 15

    # --- Good keyword bonus ---
    for word in _GOOD_KEYWORDS:
        if word in title:
            score += 5

    # --- Prefer videos where title is close to just track + artist ---
    # Penalise extra words in title (longer titles are often compilations / parodies / karaoke)
    extra_words = len(title.split()) - len(track_name.split()) - len(artist_name.split())
    if extra_words > 3:
        score -= min(extra_words * 2, 20)

    # --- Duration checks ---
    duration = entry.get("duration")
    if duration:
        if duration < MIN_VIDEO_DURATION:
            score -= 20
        elif duration > MAX_VIDEO_DURATION:
            score -= 25
        elif 120 <= duration <= 420:
            score += 6

    # --- Strong duration match bonus ---
    if expected_seconds and duration:
        diff = abs(duration - expected_seconds)
        if diff < 5:
            score += 35
        elif diff < 15:
            score += 25
        elif diff < 30:
            score += 15
        elif diff < 60:
            score += 8

    # --- View count popularity bonus (official videos often have views) ---
    view_count = entry.get("view_count") or 0
    if isinstance(view_count, int) and view_count > 0:
        if view_count > 10_000_000:
            score += 8
        elif view_count > 1_000_000:
            score += 5
        elif view_count > 100_000:
            score += 2

    # --- If track name completely missing from title, heavy penalty ---
    if track_name not in title:
        score -= 15

    return score


def get_best_youtube_result(track_name, artist_name, expected_seconds=None):
    """Search YouTube and return the best matching, available video."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        "ignoreerrors": True,
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
    }
    # Use quotes around track name to reduce irrelevant matches.
    # Queries ordered from most precise to broadest.
    safe_track = track_name.replace('"', '')
    safe_artist = artist_name.replace('"', '')
    queries = [
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist}',
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist} official audio',
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist} lyrics',
        f'ytsearch{SEARCH_RESULTS}:{safe_track} {safe_artist}',  # last resort broad query
    ]

    all_candidates = []
    for query in queries:
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception:
            continue
        for entry in info.get("entries") or []:
            if not entry or not entry.get("id"):
                continue
            duration = entry.get("duration")
            if entry.get("is_live"):
                continue
            if duration is not None and (duration < MIN_VIDEO_DURATION or duration > MAX_VIDEO_DURATION):
                continue
            all_candidates.append(entry)

    if not all_candidates:
        return None

    all_candidates.sort(
        key=lambda e: _score_result(e, track_name, artist_name, expected_seconds),
        reverse=True,
    )

    check_opts = {"quiet": True, "no_warnings": True, "cookiefile": None}
    if FFMPEG_LOCATION:
        check_opts["ffmpeg_location"] = FFMPEG_LOCATION

    for entry in all_candidates:
        url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry['id']}"
        try:
            with YoutubeDL(check_opts) as ydl:
                ydl.extract_info(url, download=False)
        except Exception:
            video_id = entry.get("id")
            print(f"[spotify] Skipping unavailable/id={video_id}", flush=True)
            continue
        return {
            "url": url,
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
    failure_log = _failure_log_path(folder)
    failure_log.touch(exist_ok=True)
    failures = []
    tracks = _tracks(client, playlist_url)
    total = len(tracks)

    lock = threading.Lock()
    completed = [0]

    def _progress_step(title):
        with lock:
            completed[0] += 1
            if progress:
                progress(completed[0], total, title)

    def _process_one(item):
        track = item.get("track") or {}
        name = (track.get("name") or "Unknown track").strip()
        artists = track.get("artists") or []
        artist = artists[0].get("name") if artists else "Unknown artist"
        if artist:
            artist = artist.strip()
        # Use the clean track name as the download title so the DB title stays
        # separate from the artist field.
        duration_ms = track.get("duration_ms")
        expected_seconds = duration_ms / 1000.0 if duration_ms else None

        try:
            candidate = get_best_youtube_result(name, artist, expected_seconds)
            if not candidate:
                raise RuntimeError("No short YouTube match found")
            file_path = download_audio_to_folder(candidate["url"], name, folder, use_yt_thumbnail=False, skip_metadata_update=True)
            if not file_path:
                raise RuntimeError("Audio download failed")
            images = track.get("album", {}).get("images") or []
            thumbnail = images[0].get("url") if images else candidate.get("thumbnail")
            add_downloaded_song_to_playlist(
                name, candidate["url"], playlist_id, file_path, artist, thumbnail
            )
            _progress_step(f"{name} - {artist}")
            return None
        except Exception as error:
            _progress_step(f"{name} - {artist}")
            return f"{name} - {artist}", str(error)

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(_process_one, item): item for item in tracks}
        for future in futures:
            err = future.result()
            if err:
                failures.append(err)
                _log_failure(folder, err[0], err[1])

    return playlist_id, failures, playlist_name, str(failure_log)


def _failure_log_path(folder):
    log_path = Path(folder) / FAILURE_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def _log_failure(folder, song, reason):
    timestamp = datetime.now().isoformat(timespec="seconds")
    with _failure_log_path(folder).open("a", encoding="utf-8", newline="") as log:
        log.write(f"[{timestamp}] {song}: {reason}\n")


class SpotifyImportWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(int, int, str, str)
    failed = Signal(str)

    def __init__(self, playlist_url, parent=None):
        super().__init__(parent)
        self.playlist_url = playlist_url

    def run(self):
        try:
            playlist_id, failures, playlist_name, failure_log = import_playlist(
                self.playlist_url,
                lambda current, total, title: self.progress.emit(current, total, title)
            )
            self.completed.emit(playlist_id, len(failures), playlist_name, failure_log)
        except Exception as error:
            self.failed.emit(str(error))
