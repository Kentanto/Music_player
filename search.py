from PySide6.QtCore import QThread, Signal
from yt_dlp import YoutubeDL
import warnings
from db import get_ffmpeg_location, get_ytdlp_cookie_options
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
    List of dicts with 'title', 'url', and 'thumbnail'
    """
    if not query:
        return []

    print(f"[search] Searching YouTube for: {query!r}", flush=True)
    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "extract_flat": True,  # Fast: don't fetch full details
        "no_warnings": True,
        **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        **get_ytdlp_cookie_options(),
    }

    # Use ytsearch{N}: to request multiple results from yt-dlp
    search_query = f"ytsearch{limit}:{query}"  # Fetch exactly 'limit' results

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
    except Exception as error:
        print(f"[search] Failed: {error}", flush=True)
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

        video_id = entry.get("id")
        thumbnail = entry.get("thumbnail")
        if not thumbnail and video_id:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        results.append({
            "title": entry.get("title"),
            "url": url,
            "thumbnail": thumbnail,
            "artist": (
                entry.get("artist")
                or entry.get("creator")
                or entry.get("uploader")
                or entry.get("channel")
            ),
            "duration": None  # Duration will be checked at playback
        })

    print(f"[search] Found {len(results)} result(s)", flush=True)
    for result in results:
        print(
            f"[search] {result.get('title')!r} | thumbnail={'yes' if result.get('thumbnail') else 'no'}",
            flush=True,
        )
    return results


class SearchWorker(QThread):
    """Runs a YouTube search off the UI thread so the app never freezes,
    even when yt-dlp / YouTube responds slowly."""

    search_finished = Signal(object)  # results list

    def __init__(self, query, parent=None):
        super().__init__(parent)
        self._query = query

    def run(self):
        try:
            results = search_youtube(self._query)
        except Exception as error:
            print(f"[search] worker failed: {error}", flush=True)
            results = []
        self.search_finished.emit(results or [])