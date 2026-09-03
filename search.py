from yt_dlp import YoutubeDL
import warnings
from db import get_ffmpeg_location
FFMPEG_LOCATION = get_ffmpeg_location()

# Suppress yt-dlp warnings
warnings.filterwarnings("ignore")


def search_youtube(query, limit=10, max_duration=600):
    """
    Search YouTube for videos (fast - no duration check here).
    Duration validation happens at playback time.
    
    Args:
        query: Search term
        limit: Number of results to fetch
        max_duration: Not used in search, kept for compatibility
    
    Returns:
        List of dicts with 'title', 'url'
    """
    if not query:
        return []

    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "extract_flat": True,  # Fast: don't fetch full details
        "no_warnings": True,
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
    }

    # Use ytsearch{N}: to request multiple results from yt-dlp
    search_query = f"ytsearch{limit}:{query}"  # Fetch exactly 'limit' results

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
    except Exception:
        return []

    entries = info.get("entries") or []
    results = []

    for entry in entries:
        if not entry:
            continue

        # entry may be flat (no webpage_url) so try several fields
        url = entry.get("webpage_url") or entry.get("url")
        if not url and entry.get("id"):
            url = f"https://www.youtube.com/watch?v={entry.get('id')}"

        if not url:
            continue

        results.append({
            "title": entry.get("title"),
            "url": url,
            "duration": None  # Duration will be checked at playback
        })

    return results