import os
import re
import sys
import sqlite3
import urllib.request
import shutil
from pathlib import Path

def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        BASE_DIR = Path(sys._MEIPASS)
    else:
        BASE_DIR = Path(__file__).resolve().parent

    return BASE_DIR / relative_path


def data_path(relative_path):
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            BASE_DIR = Path(sys.executable).resolve().parent
        else:
            BASE_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "music_engine"
    else:
        BASE_DIR = Path(__file__).resolve().parent

    path = BASE_DIR / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_ffmpeg_location():
    bundled = resource_path("ffmpeg/bin")
    if os.name == "nt" and (bundled / "ffmpeg.exe").exists():
        return str(bundled)
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    return str(bundled) if bundled.exists() else None


FFMPEG_LOCATION = get_ffmpeg_location()
DB = data_path("music.db")
PLAYLISTS_DIR = data_path("playlists")
PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_filename(value):
    value = str(value).strip()
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", " ", value)
    return value[:240].strip()


def thumbnail_path_for_audio(file_path):
    """Return the sidecar artwork path used for a local audio file."""
    return str(Path(file_path).with_suffix(".jpg"))


def download_thumbnail(thumbnail_url, file_path):
    """Download artwork beside an audio file, skipping an existing image."""
    if not thumbnail_url or not file_path:
        print(f"[thumbnail] Skipped: no URL for {file_path}", flush=True)
        return None

    target = Path(thumbnail_path_for_audio(file_path))
    if target.exists():
        print(f"[thumbnail] Already exists: {target}", flush=True)
        return str(target)

    try:
        print(f"[thumbnail] Downloading {thumbnail_url} -> {target}", flush=True)
        request = urllib.request.Request(
            thumbnail_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            target.write_bytes(response.read())
        print(f"[thumbnail] Saved: {target}", flush=True)
        return str(target)
    except Exception as error:
        print(f"[thumbnail] Failed for {file_path}: {error}", flush=True)
        target.unlink(missing_ok=True)
        return None


def init_db():
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            url TEXT UNIQUE,
            artist TEXT,
            thumbnail TEXT
        )
        """)

        columns = {row[1] for row in c.execute("PRAGMA table_info(songs)")}
        if "artist" not in columns:
            c.execute("ALTER TABLE songs ADD COLUMN artist TEXT")
        if "thumbnail" not in columns:
            c.execute("ALTER TABLE songs ADD COLUMN thumbnail TEXT")

        c.execute("""
        CREATE TABLE IF NOT EXISTS stream_cache (
            url TEXT PRIMARY KEY,
            stream_url TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            folder TEXT UNIQUE
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS playlist_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER,
            song_id INTEGER,
            file_path TEXT UNIQUE,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id),
            FOREIGN KEY (song_id) REFERENCES songs(id),
            UNIQUE (playlist_id, song_id)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # Register any existing playlist folders on disk.
        for folder in sorted(PLAYLISTS_DIR.iterdir()):
            if folder.is_dir():
                c.execute(
                    "INSERT OR IGNORE INTO playlists (name, folder) VALUES (?, ?)",
                    (folder.name, str(folder))
                )
        conn.commit()


def save_song(title, url, artist=None, thumbnail=None):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO songs (title, url, artist, thumbnail) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET "
            "title=COALESCE(excluded.title, songs.title), "
            "artist=COALESCE(excluded.artist, songs.artist), "
            "thumbnail=COALESCE(excluded.thumbnail, songs.thumbnail)",
            (title, url, artist, thumbnail)
        )
        c.execute("SELECT id FROM songs WHERE url=?", (url,))
        row = c.fetchone()
        return row[0] if row else None


def get_songs():
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("SELECT title, url FROM songs")
        return c.fetchall()


def create_playlist(name):
    name = str(name).strip()
    if not name:
        return None

    folder = PLAYLISTS_DIR / _sanitize_filename(name)
    folder.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO playlists (name, folder) VALUES (?, ?)",
            (name, str(folder))
        )
        conn.commit()
        c.execute("SELECT id, name, folder FROM playlists WHERE name=?", (name,))
        return c.fetchone()


def get_playlists():
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT p.id, p.name, p.folder, COUNT(ps.id) "
            "FROM playlists p "
            "LEFT JOIN playlist_songs ps ON p.id = ps.playlist_id "
            "GROUP BY p.id ORDER BY p.name"
        )
        playlists = c.fetchall()

    results = []
    for playlist_id, name, folder, count in playlists:
        if count == 0:
            count = len(_scan_playlist_folder(folder))
        else:
            count = max(count, len(_scan_playlist_folder(folder)))
        results.append((playlist_id, name, folder, count))

    return results


def get_playlist_by_id(playlist_id):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, folder FROM playlists WHERE id=?", (playlist_id,))
        return c.fetchone()


def _scan_playlist_folder(folder):
    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        return []

    audio_extensions = {".mp3", ".m4a", ".opus", ".wav", ".aac"}
    songs = []
    for file_path in sorted(folder_path.iterdir()):
        if file_path.suffix.lower() in audio_extensions:
            songs.append((file_path.stem, str(file_path), str(file_path)))
    return songs


def set_app_setting(key, value):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )
        conn.commit()


def get_app_setting(key, default=None):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key=?", (key,))
        row = c.fetchone()
        return row[0] if row is not None else default


def get_playlist_songs(playlist_id):
    playlist = get_playlist_by_id(playlist_id)
    if not playlist:
        return []

    _, _, folder = playlist
    folder_songs = _scan_playlist_folder(folder)

    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT s.title, s.url, ps.file_path "
            "FROM playlist_songs ps "
            "JOIN songs s ON ps.song_id = s.id "
            "WHERE ps.playlist_id = ? "
            "ORDER BY ps.id",
            (playlist_id,)
        )
        db_songs = c.fetchall()

    if not db_songs:
        return folder_songs

    songs_by_path = {song[2]: song for song in db_songs}
    merged = list(db_songs)

    for title, file_path, _ in folder_songs:
        if file_path not in songs_by_path:
            merged.append((title, None, file_path))

    return merged


def get_playlist_song(playlist_id, song_id):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT file_path FROM playlist_songs WHERE playlist_id=? AND song_id=?",
            (playlist_id, song_id)
        )
        row = c.fetchone()
        return row[0] if row else None


def remove_playlist_song(playlist_id, file_path, delete_file=True):
    """Remove a playlist entry and optionally delete its local audio file."""
    if not file_path:
        return False

    path = Path(file_path)
    if delete_file:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False

    with sqlite3.connect(DB) as conn:
        conn.execute(
            "DELETE FROM playlist_songs WHERE playlist_id=? AND file_path=?",
            (playlist_id, str(file_path)),
        )
        conn.commit()
    return True


def add_song_to_playlist(title, url, playlist_id):
    song_id = save_song(title, url)
    if not song_id:
        return None

    existing_file = get_playlist_song(playlist_id, song_id)
    if existing_file and os.path.exists(existing_file):
        return existing_file

    playlist = get_playlist_by_id(playlist_id)
    if not playlist:
        return None

    _, _, folder = playlist
    if not folder:
        return None

    file_path = download_audio_to_folder(url, title, folder)
    if not file_path:
        return None

    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, file_path) VALUES (?, ?, ?)",
            (playlist_id, song_id, file_path)
        )
        conn.commit()
    return file_path


def add_downloaded_song_to_playlist(title, url, playlist_id, file_path, artist=None, thumbnail=None):
    """Register an existing download and preserve metadata from an external catalog."""
    song_id = save_song(title, url, artist, thumbnail)
    if not song_id or not file_path:
        return None

    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, file_path) VALUES (?, ?, ?)",
            (playlist_id, song_id, file_path),
        )
        conn.commit()
    download_thumbnail(thumbnail, file_path)
    return file_path


def download_audio_to_folder(url, title, folder):
    from yt_dlp import YoutubeDL
    import warnings

    warnings.filterwarnings("ignore")
    os.makedirs(folder, exist_ok=True)

    safe_title = _sanitize_filename(title) or "audio"
    out_template = os.path.join(folder, f"{safe_title} - %(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio[protocol=https]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "js_runtimes": {"node": {}},
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        "outtmpl": out_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    browser = os.environ.get("YTDLP_BROWSER")
    if browser:
        ydl_opts["cookiesfrombrowser"] = (browser,)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get("duration", 0)
            if duration and duration > 600:
                return None

            info = ydl.extract_info(url, download=True)
            video_id = info.get("id")
            if not video_id:
                return None

            target_path = os.path.join(folder, f"{safe_title} - {video_id}.mp3")
            if os.path.exists(target_path):
                download_thumbnail(info.get("thumbnail"), target_path)
                update_song_metadata(url, info.get("title") or title, _artist_from_info(info), info.get("thumbnail"))
                return target_path

            for ext in ["mp3", "m4a", "opus", "wav", "aac"]:
                alt_path = os.path.join(folder, f"{safe_title} - {video_id}.{ext}")
                if os.path.exists(alt_path):
                    download_thumbnail(info.get("thumbnail"), alt_path)
                    update_song_metadata(url, info.get("title") or title, _artist_from_info(info), info.get("thumbnail"))
                    return alt_path
    except Exception:
        return None

    return None


def _artist_from_info(info):
    """Choose the most useful artist field yt-dlp exposes for a video."""
    return (
        info.get("artist")
        or info.get("creator")
        or info.get("uploader")
        or info.get("channel")
    )


def backfill_thumbnails():
    """Fetch missing artwork for songs already registered in playlists."""
    from yt_dlp import YoutubeDL

    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT s.url, ps.file_path FROM playlist_songs ps "
            "JOIN songs s ON ps.song_id = s.id WHERE s.url IS NOT NULL"
        ).fetchall()

    # Older library files may have been created by folder scanning and have
    # no playlist_songs row. Their download template still includes the ID.
    known_paths = {file_path for _, file_path in rows}
    video_id_pattern = re.compile(r"[- ]([A-Za-z0-9_-]{11})\.(?:mp3|m4a|opus|wav|aac)$", re.IGNORECASE)
    for folder in PLAYLISTS_DIR.iterdir():
        if not folder.is_dir():
            continue
        for file_path in folder.iterdir():
            if file_path.suffix.lower() not in {".mp3", ".m4a", ".opus", ".wav", ".aac"}:
                continue
            if str(file_path) in known_paths:
                continue
            match = video_id_pattern.search(file_path.name)
            if match:
                rows.append((f"https://www.youtube.com/watch?v={match.group(1)}", str(file_path)))
            else:
                rows.append((None, str(file_path)))

    print(f"[thumbnail-backfill] Found {len(rows)} registered song(s)", flush=True)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": True,
        "ffmpeg_location": FFMPEG_LOCATION,
    }
    with YoutubeDL(ydl_opts) as ydl:
        for url, file_path in rows:
            if not file_path or not os.path.exists(file_path):
                print(f"[thumbnail-backfill] Skipped missing file: {file_path}", flush=True)
                continue
            metadata = get_track_metadata(file_path)
            if metadata.get("artist") and metadata.get("thumbnail"):
                print(f"[thumbnail-backfill] Already has artwork: {file_path}", flush=True)
                continue
            try:
                lookup_url = url
                if not lookup_url:
                    query = Path(file_path).stem
                    print(f"[thumbnail-backfill] Searching title: {query}", flush=True)
                    search_info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                    entry = next(iter(search_info.get("entries") or []), None)
                    if not entry or not entry.get("id"):
                        print(f"[thumbnail-backfill] No match: {query}", flush=True)
                        continue
                    lookup_url = f"https://www.youtube.com/watch?v={entry['id']}"

                print(f"[thumbnail-backfill] Looking up: {lookup_url}", flush=True)
                info = ydl.extract_info(lookup_url, download=False)
                if not info.get("thumbnail") and info.get("id"):
                    info["thumbnail"] = f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
                download_thumbnail(info.get("thumbnail"), file_path)
                title = info.get("title") or Path(file_path).stem
                artist = _artist_from_info(info)
                song_id = save_song(title, lookup_url, artist, info.get("thumbnail"))
                playlist_row = None
                with sqlite3.connect(DB) as conn:
                    playlist_row = conn.execute(
                        "SELECT id FROM playlists WHERE folder=?",
                        (str(Path(file_path).parent),),
                    ).fetchone()
                    if song_id and playlist_row:
                        conn.execute(
                            "INSERT OR IGNORE INTO playlist_songs "
                            "(playlist_id, song_id, file_path) VALUES (?, ?, ?)",
                            (playlist_row[0], song_id, str(file_path)),
                        )
                    conn.commit()
            except Exception as error:
                print(f"[thumbnail-backfill] Failed for {file_path}: {error}", flush=True)
                continue

    print("[thumbnail-backfill] Finished", flush=True)


def cache_stream(url, stream_url):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO stream_cache VALUES (?, ?)",
            (url, stream_url)
        )


def get_cached_stream(url):
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()
        c.execute("SELECT stream_url FROM stream_cache WHERE url=?", (url,))
        row = c.fetchone()
        return row[0] if row else None


def update_song_metadata(url, title=None, artist=None, thumbnail=None):
    """Update metadata for a song already linked by its YouTube URL."""
    if not url:
        return

    with sqlite3.connect(DB) as conn:
        conn.execute(
            "UPDATE songs SET title=COALESCE(?, title), artist=COALESCE(?, artist), "
            "thumbnail=COALESCE(?, thumbnail) WHERE url=?",
            (title, artist, thumbnail, url),
        )
        conn.commit()


def get_track_metadata(file_path):
    """Return database metadata linked to a downloaded audio file."""
    if not file_path:
        return {}

    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT s.title, s.artist, s.thumbnail, s.url "
            "FROM playlist_songs ps JOIN songs s ON s.id = ps.song_id "
            "WHERE ps.file_path=? LIMIT 1",
            (str(file_path),),
        ).fetchone()
    if not row:
        return {}
    return {"title": row[0], "artist": row[1], "thumbnail": row[2], "url": row[3]}