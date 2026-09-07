"""Background metadata fetcher - validates video durations without blocking UI"""

from PySide6.QtCore import QThread, Signal
from yt_dlp import YoutubeDL
import warnings

from db import get_ffmpeg_location
FFMPEG_LOCATION = get_ffmpeg_location()

warnings.filterwarnings("ignore")


class MetadataFetcher(QThread):
    """Fetches video metadata (duration) in background"""
    
    video_too_long = Signal(str)  # Emitted with URL when video > max_duration
    fetching_done = Signal()  # Emitted when all videos checked
    
    def __init__(self, results, max_duration=600):
        super().__init__()
        self.results = results  # List of dicts with 'url', 'title'
        self.max_duration = max_duration
        self.should_stop = False
    
    def run(self):
        """Fetch metadata for each result"""
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
                    duration = info.get("duration", 0)
                    
                    # If video is too long, signal to remove it
                    if duration and duration > self.max_duration:
                        self.video_too_long.emit(url)
            except Exception:
                # Silently skip errors (network, deleted videos, etc.)
                pass
        
        self.fetching_done.emit()
    
    def stop(self):
        """Stop the thread gracefully"""
        self.should_stop = True
        self.wait()
