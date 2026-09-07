"""Background metadata fetcher - validates video durations without blocking UI"""

from PySide6.QtCore import QThread, Signal
from yt_dlp import YoutubeDL
import warnings

from db import get_ffmpeg_location
FFMPEG_LOCATION = get_ffmpeg_location()

warnings.filterwarnings("ignore")


class MetadataFetcher(QThread):
    """Fetches video metadata (duration, artist) in background."""

    video_too_long = Signal(str)
    metadata_ready = Signal(str, object)  # url, {duration, artist, thumbnail}
    fetching_done = Signal()

    def __init__(self, results, max_duration=600):
        super().__init__()
        self.results = results
        self.max_duration = max_duration
        self.should_stop = False

    def run(self):
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            **({"ffmpeg_location": FFMPEG_LOCATION} if FFMPEG_LOCATION else {}),
        }

        for result in self.results:
            if self.should_stop:
                break

            url = result.get("url")
            if not url:
                continue

            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                continue

            duration = info.get("duration", 0)
            if duration and duration > self.max_duration:
                self.video_too_long.emit(url)
                continue

            enriched = {
                "duration": duration,
                "artist": (
                    info.get("artist")
                    or info.get("creator")
                    or info.get("uploader")
                    or info.get("channel")
                ),
                "thumbnail": info.get("thumbnail"),
            }
            self.metadata_ready.emit(url, enriched)

        self.fetching_done.emit()

    def stop(self):
        self.should_stop = True
        self.wait()
