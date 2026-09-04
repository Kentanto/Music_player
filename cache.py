import os
import tempfile
import warnings
from pathlib import Path
from yt_dlp import YoutubeDL
from db import get_cached_stream, cache_stream, resource_path


FFMPEG_LOCATION = resource_path("ffmpeg/bin")

# Suppress warnings
warnings.filterwarnings("ignore")

# Use system temp directory for audio cache
CACHE_DIR = Path(tempfile.gettempdir()) / "music_platform_cache"
CACHE_DIR.mkdir(exist_ok=True)


def resolve_stream(url, max_duration=600):
    """
    Download audio to local cache and return file path (avoids stream expiration).
    Skips videos longer than max_duration.
    """
    cached = get_cached_stream(url)
    if cached and os.path.exists(cached):
        return cached

    ydl_opts = {
        "format": "bestaudio[protocol=https]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "js_runtimes": {"node": {}},
        "ffmpeg_location": FFMPEG_LOCATION,
        "outtmpl": str(CACHE_DIR / "%(id)s.%(ext)s"),
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

    # Add common browser headers
    ydl_opts.setdefault("http_headers", {})
    ydl_opts["http_headers"].setdefault(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36",
    )
    ydl_opts["http_headers"].setdefault("Referer", "https://www.youtube.com/")

    try:
        with YoutubeDL(ydl_opts) as ydl:
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