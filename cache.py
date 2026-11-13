import os
import shutil
import tempfile
import warnings
from pathlib import Path
from yt_dlp import YoutubeDL
from db import get_cached_stream, cache_stream, resource_path, get_ytdlp_cookie_options, YTDLP_HTTP_HEADERS


def get_ffmpeg_location():
    bundled = resource_path("ffmpeg/bin")
    if os.name == "nt" and (bundled / "ffmpeg.exe").exists():
        return str(bundled)
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    return str(bundled) if bundled.exists() else None


FFMPEG_LOCATION = get_ffmpeg_location()

# Suppress warnings
warnings.filterwarnings("ignore")

# Use system temp directory for audio cache
CACHE_DIR = Path(tempfile.gettempdir()) / "music_platform_cache"
CACHE_DIR.mkdir(exist_ok=True)


def cached_stream_path(url):
    """Return the already-downloaded local cache file for url, or None."""
    cached = get_cached_stream(url)
    if cached and os.path.exists(cached):
        return cached
    return None


def probe_stream_url(url, max_duration=600):
    """
    Quickly resolve the direct network audio URL (no download) so playback
    can start immediately while the full file downloads to the cache in
    parallel.  Skips live streams and videos longer than max_duration.
    """
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[protocol^=https]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "js_runtimes": {"node": {}},
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
    }

    ydl_opts.update(get_ytdlp_cookie_options())

    # Send the same browser headers as every other yt-dlp call in the app.
    ydl_opts.setdefault("http_headers", {}).update(YTDLP_HTTP_HEADERS)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None

    if not info or info.get("is_live"):
        return None

    duration = info.get("duration")
    if duration and duration > max_duration:
        print(f"Video too long ({duration}s > {max_duration}s), skipping", flush=True)
        return None

    # yt-dlp merges the chosen format into the info dict; its direct URL is
    # what the player streams while the download runs.
    stream_url = info.get("url")
    if not stream_url:
        # Fallback: pick a direct http audio format ourselves.
        for fmt in reversed(info.get("formats") or []):
            if not fmt.get("url"):
                continue
            if fmt.get("vcodec") not in (None, "none"):
                continue
            if (fmt.get("protocol") or "").lower().startswith("http"):
                stream_url = fmt.get("url")
                break
    return stream_url or None


def resolve_stream(url, max_duration=600, probe=True):
    """
    Download audio to local cache and return file path (avoids stream expiration).
    Skips videos longer than max_duration (checked during the probe pass).
    Pass probe=False when the URL was already probed to skip the duplicate
    network round-trip.
    """
    cached = cached_stream_path(url)
    if cached:
        return cached

    ydl_opts = {
        "format": "bestaudio[protocol=https]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "js_runtimes": {"node": {}},
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        "outtmpl": str(CACHE_DIR / "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    ydl_opts.update(get_ytdlp_cookie_options())

    # Send the same browser headers as every other yt-dlp call in the app.
    ydl_opts.setdefault("http_headers", {}).update(YTDLP_HTTP_HEADERS)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            if probe:
                info = ydl.extract_info(url, download=False)
                duration = info.get("duration", 0)

                # Skip if video is too long
                if duration and duration > max_duration:
                    print(f"Video too long ({duration}s > {max_duration}s), skipping")
                    return None

            # Now download
            info = ydl.extract_info(url, download=True)
            video_id = info.get("id")
            
            if not video_id:
                return None
            
            # Look for the downloaded audio file (mp3 or original audio)
            mp3_file = CACHE_DIR / f"{video_id}.mp3"
            if mp3_file.exists():
                file_path = str(mp3_file)
                cache_stream(url, file_path)
                return file_path
            
            # Fallback: look for any audio file with that id
            for ext in ["m4a", "opus", "vorbis", "wav", "aac"]:
                alt_file = CACHE_DIR / f"{video_id}.{ext}"
                if alt_file.exists():
                    file_path = str(alt_file)
                    cache_stream(url, file_path)
                    return file_path

    except Exception:
        return None

    return None