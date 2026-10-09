"""Import a Spotify playlist into the Music Engine library."""

import os
import re
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
    YTDLP_HTTP_HEADERS,
    BotBlockedError,
    _is_bot_block_error,
    add_downloaded_song_to_playlist,
    add_spotify_link,
    create_playlist,
    download_audio_to_folder,
    get_playlist_by_id,
    get_spotify_links,
    get_spotify_track_statuses,
    mark_spotify_tracks_seen,
    set_spotify_link_checked,
    set_spotify_track_status,
)

# These values are portable across operating systems. Keep them in this small
# configuration section so the importer does not depend on shell variables.
SPOTIFY_CLIENT_ID = "bdb2e21d2b384b31bea7e4c622140d81"
SPOTIFY_CLIENT_SECRET = "9fca954348d84b658aeb2987a068ef69"
PLAYLIST_NAME = "Spotify Import"
MIN_VIDEO_DURATION = 45
MAX_VIDEO_DURATION = 10 * 60
SEARCH_RESULTS = 10          # results to ask YouTube for per search query
NUM_WORKERS = 1              # one at a time to avoid bot detection
BATCH_DELAY = 0.5            # seconds between each batch
FAILURE_LOG_NAME = "failed_songs.txt"

# Tiny title filters.  YouTube's own ranking already puts the original upload
# on top for a clean "track + artist" query, so all we do is skip the obvious
# junk.  If a word is part of the track or artist name itself (e.g. a song
# called "Live Forever" or an official "(Acoustic)" version), it is NOT used
# as a filter for that track.
_FILTER_TITLE_WORDS = [
    "karaoke", "instrumental", "backing track", "cover", "covers", "live",
    "react", "reaction", "review", "tutorial", "how to", "parody", "meme",
    "nightcore", "daycore", "8d", "sped up", "spedup", "speed up", "slowed",
    "bass boosted", "reverb", "loop", "mashup", "remix", "remixes",
    "extended", "acoustic", "unplugged",
]
_FILTER_PATTERNS = [re.compile(r"\b" + re.escape(word) + r"\b") for word in _FILTER_TITLE_WORDS]
# "1 hour", "10 minutes", "3 min" style loop uploads
_FILTER_PATTERNS.append(re.compile(r"\b\d+\s*(?:hours?|mins?|minutes?)\b"))


def _playlist_id(url):
    return url.split("playlist/", 1)[1].split("?", 1)[0] if "playlist/" in url else url


def _spotify_client():
    return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
    ))


def _title_is_filtered(title, track_name, artist_name):
    """Return True when a result title looks like a live or altered version.

    Only obvious junk is filtered (karaoke, nightcore, hour-long loops, ...).
    Words that appear in the track or artist name itself never count, so a
    song genuinely called "Live Forever" still matches normally.
    """
    title = (title or "").lower().strip()
    if not title:
        return False
    name = f"{track_name} {artist_name}".lower()
    for pattern in _FILTER_PATTERNS:
        if pattern.search(title) and not pattern.search(name):
            return True
    return False


def _duration_ok(duration, expected_seconds=None):
    """Tiny length sanity check: no tiny clips, no hour-long uploads.

    When Spotify's track length is known we also skip results wildly off it
    (e.g. a 'song X for 10 minutes' loop), but normal music-video intro and
    outro differences are far within the tolerance.
    """
    if duration < MIN_VIDEO_DURATION or duration > MAX_VIDEO_DURATION:
        return False
    if expected_seconds:
        if duration > expected_seconds * 2.5 or duration * 2.5 < expected_seconds:
            return False
    return True


def get_best_youtube_result(track_name, artist_name, expected_seconds=None):
    """Search YouTube with clean keywords and return its first good result.

    YouTube's own search ranking already puts the original upload on top for
    a clean "track + artist" query, so we trust it and simply walk down the
    results, taking the first one that passes a few tiny filters (no live
    streams, sane length, no karaoke/altered versions) and is downloadable.

    Returns (candidate, blocked_count). blocked_count counts results refused
    by YouTube's 'confirm you're not a bot' wall — when every result was
    blocked the caller should treat it as a cookie problem, not as
    "song not found".
    """

    from db import get_ytdlp_cookie_options
    cookie_opt = get_ytdlp_cookie_options()
    # Same "look like a normal browser" setup the streaming cache uses, so
    # the requests are not flagged as bot traffic.
    shared_opts = {
        "http_headers": dict(YTDLP_HTTP_HEADERS),
        **cookie_opt,
    }
    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
        "ignoreerrors": True,
        **shared_opts,
    }
    full_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Raise instead of silently returning None so a bot-wall block can
        # be told apart from an ordinary unusable video.
        "ignoreerrors": False,
        "retries": 3,
        "js_runtimes": {"node": {}},
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        **shared_opts,
    }

    safe_track = track_name.replace('"', '')
    safe_artist = artist_name.replace('"', '')
    queries = [
        f'ytsearch{SEARCH_RESULTS}:"{safe_track}" {safe_artist}',
        f'ytsearch{SEARCH_RESULTS}:{safe_track} {safe_artist}',
    ]

    # ---- Walk YouTube's results in order and take the first good one ----
    t0 = time.time()
    seen_ids = set()
    blocked = 0
    for query in queries:
        try:
            with YoutubeDL(flat_opts) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception:
            continue

        for e in (info.get("entries") or []) if info else []:
            if not e or not e.get("id") or e["id"] in seen_ids:
                continue
            seen_ids.add(e["id"])

            # ---- tiny filters: live streams, silly lengths, karaoke-style titles ----
            if e.get("is_live"):
                continue
            dur = e.get("duration")
            if dur is not None and not _duration_ok(dur, expected_seconds):
                continue
            if _title_is_filtered(e.get("title"), track_name, artist_name):
                continue

            # ---- verify the pick is actually playable before returning it ----
            url = e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}"
            try:
                with YoutubeDL(full_opts) as ydl:
                    full = ydl.extract_info(url, download=False)
            except Exception as error:
                if _is_bot_block_error(error):
                    blocked += 1
                continue
            if not full or full.get("is_live"):
                continue
            dur = full.get("duration")
            if dur is not None and not _duration_ok(dur, expected_seconds):
                continue

            uploader = (
                full.get("uploader")
                or full.get("channel")
                or full.get("creator")
                or full.get("artist")
            )
            print(
                f"[search] '{track_name}' done in {(time.time()-t0):.2f}s → {full.get('title', 'unknown')!r} "
                f"(uploader={uploader or 'unknown'}, views={full.get('view_count') or 'unknown'})",
                flush=True,
            )
            return {
                "url": full.get("webpage_url") or url,
                "thumbnail": full.get("thumbnail"),
                "uploader": uploader,
                "view_count": full.get("view_count"),
            }, blocked

    print(f"[search] '{track_name}' done in {(time.time()-t0):.2f}s → no usable result", flush=True)
    if blocked:
        print(f"[search] {blocked} result(s) were blocked by YouTube's bot check", flush=True)
    return None, blocked


def _tracks(client, playlist_url):
    results = client.playlist_tracks(_playlist_id(playlist_url))
    tracks = list(results.get("items") or [])
    while results.get("next"):
        results = client.next(results)
        tracks.extend(results.get("items") or [])
    return tracks


def _track_identity(track):
    """Return (track_id, name, artist) for a Spotify track object."""
    name = (track.get("name") or "Unknown track").strip()
    artists = track.get("artists") or []
    artist = (artists[0].get("name") if artists else None) or "Unknown artist"
    return track.get("id"), name, artist.strip()


def _download_track_to_playlist(track, playlist_id, folder):
    """Pick a YouTube result for a Spotify track, download and register it.

    Raises on failure. Shared by the importer and the linked-playlist sync so
    both use the exact same download path. The clean track name is used as
    the download title so the DB title stays separate from the artist field.
    """
    _, name, artist = _track_identity(track)
    duration_ms = track.get("duration_ms")
    expected_seconds = duration_ms / 1000.0 if duration_ms else None

    candidate, blocked_count = get_best_youtube_result(name, artist, expected_seconds)
    if not candidate:
        if blocked_count:
            raise BotBlockedError("YouTube's sign-in bot check refused every result")
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
    return name, artist


def import_playlist(playlist_url, target_playlist_id=None, progress=None, should_stop=None):
    """Download a Spotify playlist and return (playlist_id, failures, name).

    If *target_playlist_id* is given, songs are appended to that existing
    playlist instead of creating a new one.
    If *should_stop* is given (a callable returning bool), the import stops
    early, between tracks, once it returns True.
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
        _, name, artist = _track_identity(track)
        label = f"{name} - {artist}"

        try:
            _download_track_to_playlist(track, playlist_id, folder)
            _progress_step(label)
            return None
        except Exception as error:
            _progress_step(label)
            return label, str(error)

    for i in range(0, len(tracks), NUM_WORKERS):
        if should_stop and should_stop():
            print("[spotify] stop requested; finishing import early", flush=True)
            break
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


def link_playlist(playlist_url, program_playlist_id):
    """Connect a Spotify playlist to a program playlist and snapshot it.

    Nothing is downloaded here: the tracks currently in the Spotify playlist
    are only remembered, so the startup sync downloads just the songs added
    to Spotify AFTER this link was made. This keeps a big existing program
    playlist from being re-downloaded wholesale.

    Returns (program_playlist_id, spotify_name, snapshot_count).
    """
    spotify_id = _playlist_id(playlist_url)
    client = _spotify_client()
    spotify_meta = client.playlist(spotify_id, fields="name")
    spotify_name = spotify_meta.get("name") or PLAYLIST_NAME

    playlist_row = get_playlist_by_id(program_playlist_id)
    if not playlist_row:
        raise RuntimeError(f"Playlist id={program_playlist_id} does not exist")

    tracks = _tracks(client, spotify_id)
    identities = [_track_identity(item.get("track") or {}) for item in tracks]
    mark_spotify_tracks_seen(program_playlist_id, identities)
    add_spotify_link(spotify_id, playlist_url, program_playlist_id)
    return program_playlist_id, spotify_name, len(identities)


def sync_linked_playlists(progress=None, should_stop=None):
    """Check every linked Spotify playlist and download only brand-new tracks.

    Meant to run once at app startup. Per link it fetches the current Spotify
    tracks and downloads only IDs never seen for that program playlist — the
    seen table is keyed per program playlist, so two Spotify lists feeding
    one program playlist never double-download the same song.

    Every attempt is final: a track that downloaded or failed once is never
    tried again, even if its local file is later deleted. One broken link
    (deleted playlist, network error) does not stop the other links, and one
    failed song does not stop the rest.

    Returns one summary per link:
    (program_playlist_id, playlist_name, added, failed, blocked, failure_log_path)

    A bot-blocked track is NOT a failure: it stays 'pending' and is retried
    on the next startup (usually fixed by setting up cookies once).
    """
    links = get_spotify_links()
    if not links:
        return []

    client = _spotify_client()
    results = []
    for link_id, spotify_id, _spotify_url, program_playlist_id, playlist_name in links:
        playlist_row = get_playlist_by_id(program_playlist_id)
        if not playlist_row:
            print(f"[spotify-sync] skipping link {link_id}: program playlist is gone", flush=True)
            continue
        _, name, folder = playlist_row
        added = 0
        failed = 0
        blocked = 0

        try:
            tracks = _tracks(client, spotify_id)
        except Exception as error:
            print(f"[spotify-sync] could not read Spotify list for '{name}': {error}", flush=True)
            _log_failure(folder, f"Spotify link check for '{name}'", str(error))
            set_spotify_link_checked(link_id)
            results.append((program_playlist_id, name, added, failed + 1, blocked, str(_failure_log_path(folder))))
            continue

        statuses = get_spotify_track_statuses(program_playlist_id)

        def track_id_of(item):
            return (item.get("track") or {}).get("id")

        # Brand-new tracks, plus any left 'pending' by an earlier bot-blocked
        # run so they get their retry now.
        to_try = [
            item for item in tracks
            if track_id_of(item) and (
                track_id_of(item) not in statuses
                or statuses[track_id_of(item)] == "pending"
            )
        ]

        # Mark them 'pending' BEFORE downloading: a crash or a YouTube bot
        # block leaves them retryable on the next startup, while a finished
        # attempt (downloaded/failed) is permanent either way.
        mark_spotify_tracks_seen(
            program_playlist_id,
            [_track_identity(item["track"]) for item in to_try],
            status="pending",
        )

        total = len(to_try)
        for index, item in enumerate(to_try, start=1):
            if should_stop and should_stop():
                print("[spotify-sync] stop requested; finishing sync early", flush=True)
                break
            track = item["track"]
            track_id, track_name, artist = _track_identity(track)
            label = f"{track_name} - {artist}"
            if progress:
                progress(index, total, label)
            try:
                _download_track_to_playlist(track, program_playlist_id, folder)
                set_spotify_track_status(program_playlist_id, track_id, "downloaded", track_name, artist)
                added += 1
            except BotBlockedError as error:
                # Not a real attempt — leave the track 'pending' so the next
                # startup retries it once cookies are set up.
                _log_failure(folder, label, f"Blocked by YouTube's bot check, will retry next start ({error})")
                blocked += 1
            except Exception as error:
                set_spotify_track_status(program_playlist_id, track_id, "failed", track_name, artist)
                _log_failure(folder, label, str(error))
                failed += 1
            time.sleep(BATCH_DELAY)

        set_spotify_link_checked(link_id)
        results.append((program_playlist_id, name, added, failed, blocked, str(_failure_log_path(folder))))
    return results


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
                should_stop=self.isInterruptionRequested,
            )
            self.completed.emit(playlist_id, len(failures), playlist_name, failure_log)
        except Exception as error:
            self.failed.emit(str(error))


class SpotifyLinkWorker(QThread):
    """Links a Spotify playlist to a program playlist off the UI thread."""

    linked = Signal(int, str, int)  # program_playlist_id, spotify name, snapshot size
    failed = Signal(str)

    def __init__(self, playlist_url, program_playlist_id, parent=None):
        super().__init__(parent)
        self.playlist_url = playlist_url
        self.program_playlist_id = program_playlist_id

    def run(self):
        try:
            program_playlist_id, spotify_name, snapshot_count = link_playlist(
                self.playlist_url, self.program_playlist_id
            )
            self.linked.emit(program_playlist_id, spotify_name, snapshot_count)
        except Exception as error:
            self.failed.emit(str(error))


class SpotifySyncWorker(QThread):
    """Runs the startup check for every linked Spotify playlist."""

    progress = Signal(int, int, str)
    completed = Signal(object)  # [(program_playlist_id, name, added, failed, blocked, failure_log)]
    failed = Signal(str)

    def run(self):
        try:
            results = sync_linked_playlists(
                progress=lambda current, total, title: self.progress.emit(current, total, title),
                should_stop=self.isInterruptionRequested,
            )
            self.completed.emit(results)
        except Exception as error:
            self.failed.emit(str(error))
