"""Import a Spotify playlist into the Music Engine library."""

import os
import time
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
    get_playlist_by_id,
)

# These values are portable across operating systems. Keep them in this small
# configuration section so the importer does not depend on shell variables.
SPOTIFY_CLIENT_ID = "bdb2e21d2b384b31bea7e4c622140d81"
SPOTIFY_CLIENT_SECRET = "9fca954348d84b658aeb2987a068ef69"
PLAYLIST_NAME = "Spotify Import"
MIN_VIDEO_DURATION = 45
MAX_VIDEO_DURATION = 10 * 60
SEARCH_RESULTS = 10          # was 20  — fewer results means faster flat searches
NUM_WORKERS = 1              # one at a time to avoid bot detection
BATCH_DELAY = 0.5            # seconds between each batch
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
    """Search YouTube and return the best matching, available video (two-phase)."""

    from db import get_ytdlp_cookie_options
    cookie_opt = get_ytdlp_cookie_options()
    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
        "ignoreerrors": True,
        **cookie_opt,
    }
    full_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": True,
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        **cookie_opt,
    }

    safe_track = track_name.replace('"', '')
    safe_artist = artist_name.replace('"', '')
    queries = [
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist}',
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist} official audio',
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist} lyrics',
        f'ytsearch{SEARCH_RESULTS}:{safe_track} {safe_artist}',
    ]

    # ---- Phase 1: fast flat search ----
    t0 = time.time()
    all_flat = []
    for query in queries:
        try:
            with YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception:
            continue
        for e in (info.get("entries") or []) if info else []:
            if not e or not e.get("id"):
                continue
            all_flat.append(e)

    if not all_flat:
        return None

    # de-dupe + pre-filter live / duration
    seen_ids = set()
    candidates = []
    for e in all_flat:
        vid = e.get("id")
        if not vid or vid in seen_ids:
            continue
        seen_ids.add(vid)
        if e.get("is_live"):
            continue
        dur = e.get("duration")
        if dur is not None and (dur < MIN_VIDEO_DURATION or dur > MAX_VIDEO_DURATION):
            continue
        candidates.append(e)

    if not candidates:
        return None

    # Rank using the SAME _score_result function (flat entries just have fewer fields,
    # missing fields default to None / 0 which is safe).
    candidates.sort(
        key=lambda e: _score_result(e, track_name, artist_name, expected_seconds),
        reverse=True,
    )

    # ---- Phase 2: full extraction on best candidates only ----
    # Try the top N.  Most tracks resolve in the first 3, but 6 gives a safety margin.
    top_n = min(6, len(candidates))
    enriched = []
    for e in candidates[:top_n]:
        url = e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}"
        try:
            with YoutubeDL(full_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            continue
        if not info or info.get("is_live"):
            continue
        dur = info.get("duration")
        if dur is not None and (dur < MIN_VIDEO_DURATION or dur > MAX_VIDEO_DURATION):
            continue
        enriched.append(info)

    if not enriched:
        return None

    enriched.sort(
        key=lambda e: _score_result(e, track_name, artist_name, expected_seconds),
        reverse=True,
    )
    winner = enriched[0]
    yt_artist = (
        winner.get("uploader")
        or winner.get("channel")
        or winner.get("creator")
        or winner.get("artist")
    )
    print(
        f"[search] '{track_name}' done in {(time.time()-t0):.2f}s → {winner.get('title', 'unknown')!r} "
        f"(uploader={winner.get('uploader') or winner.get('channel') or 'unknown'})",
        flush=True,
    )
    return {
        "url": winner.get("webpage_url") or f"https://www.youtube.com/watch?v={winner['id']}",
        "thumbnail": winner.get("thumbnail"),
        "uploader": yt_artist,
    }


def _tracks(client, playlist_url):
    results = client.playlist_tracks(_playlist_id(playlist_url))
    tracks = list(results.get("items") or [])
    while results.get("next"):
        results = client.next(results)
        tracks.extend(results.get("items") or [])
    return tracks


def import_playlist(playlist_url, target_playlist_id=None, progress=None):
    """Download a Spotify playlist and return (playlist_id, failures, name).

    If *target_playlist_id* is given, songs are appended to that existing
    playlist instead of creating a new one.
    """
    spotify_id = _playlist_id(playlist_url)
    client = _spotify_client()
    spotify_meta = client.playlist(spotify_id, fields="name")
    spotify_name = spotify_meta.get("name") or PLAYLIST_NAME

    if target_playlist_id is not None:
        playlist_row = get_playlist_by_id(target_playlist_id)
        if not playlist_row:
            raise RuntimeError(f"Playlist id={target_playlist_id} does not exist")
        playlist_id, playlist_name, folder = playlist_row
    else:
        playlist_row = create_playlist(spotify_name)
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
        artist = (artists[0].get("name") if artists else None) or "Unknown artist"
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
            yt_artist = candidate.get("uploader") or artist
            add_downloaded_song_to_playlist(
                name, candidate["url"], playlist_id, file_path, yt_artist, thumbnail
            )
            _progress_step(f"{name} - {artist}")
            return None
        except Exception as error:
            _progress_step(f"{name} - {artist}")
            return f"{name} - {artist}", str(error)

    for i in range(0, len(tracks), NUM_WORKERS):
        batch = tracks[i:i + NUM_WORKERS]
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = {executor.submit(_process_one, item): item for item in batch}
            for future in futures:
                err = future.result()
                if err:
                    failures.append(err)
                    _log_failure(folder, err[0], err[1])
        time.sleep(BATCH_DELAY)

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

    def __init__(self, playlist_url, target_playlist_id=None, parent=None):
        super().__init__(parent)
        self.playlist_url = playlist_url
        self.target_playlist_id = target_playlist_id

    def run(self):
        try:
            playlist_id, failures, playlist_name, failure_log = import_playlist(
                self.playlist_url,
                target_playlist_id=self.target_playlist_id,
                progress=lambda current, total, title: self.progress.emit(current, total, title),
            )
            self.completed.emit(playlist_id, len(failures), playlist_name, failure_log)
        except Exception as error:
            self.failed.emit(str(error))
