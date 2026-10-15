"""Retry songs that previously failed during import.

Two ways to use it:

- In the app: the "Failed Songs" dialog has a "Retry Failed Songs" button
  that retries the whole list for that playlist (RetryFailedWorker below).
- Standalone: python retry_failed.py [--playlist "Mixer"] [--browser chrome]

Either way, after the run the playlist's failed_songs.txt is cleared and
rewritten with only the songs that failed again, so the Failed Songs list
always reflects the current state. Lines in the log that are not about a
song (playlist-level notices, e.g. a failed Spotify link check) are never
retried and are kept in the log exactly as they were.
"""

import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from db import (
    BotBlockedError,
    add_downloaded_song_to_playlist,
    download_audio_to_folder,
    get_playlists,
)
from search import search_youtube

<<<<<<< HEAD
FAILURE_LOG_NAME = "failed_songs.txt"
SEARCH_LIMIT = 6         # results to ask YouTube for per song
PRE_DOWNLOAD_PAUSE = 3    # short pause before each download
DELAY_SEC = 12            # pause between songs to avoid the bot wall

# Labels that mark a failure-log entry as being about the playlist itself
# instead of a song. The Spotify sync writes one of these when it cannot
# even read the playlist from the Spotify API (timeout, deleted playlist,
# ...) — nothing about it can be downloaded, so a retry run must skip it
# and leave the line in the log untouched. spotify_download.py builds its
# link-check label from this prefix so the two can never drift apart.
PLAYLIST_NOTICE_PREFIX = "Spotify link check for '"

_FAILURE_LINE = re.compile(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] (.+?): (.+)")
=======
PLAYLIST_NAME = "Chill"
FAILURE_LOG = Path(f"playlists/{PLAYLIST_NAME}/failed_songs.txt")
RETRY_LOG = FAILURE_LOG.with_name("retry_log.txt")
DELAY_SEC = 12  # pause between songs to avoid bot wall
>>>>>>> fc47cb5 (failed songs manualy rebase)


def failure_log_path(folder):
    return Path(folder) / FAILURE_LOG_NAME


def read_failure_log(log_path):
    """Split a failure log into retryable songs and lines to leave alone.

    Returns (items, other_lines):

    - items: one entry per song that can be retried,
      [(song, reason, original_line)]. The same song can appear more than
      once in the log (one line per failed attempt); only the first
      occurrence is kept so a retry tries each song once.
    - other_lines: everything that is not a song — playlist notices (see
      PLAYLIST_NOTICE_PREFIX) and lines in an unknown format. They can
      never be retried and are written back to the log exactly as they
      were, so the Failed Songs dialog keeps showing them.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return [], []
    items = []
    other_lines = []
    seen = set()
    for original_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not original_line.strip():
            continue
        match = _FAILURE_LINE.match(original_line)
        if not match:
            other_lines.append(original_line)
            continue
        song, reason = match.group(1), match.group(2)
        if song.startswith(PLAYLIST_NOTICE_PREFIX):
            other_lines.append(original_line)
            continue
        if song in seen:
            continue
        seen.add(song)
        items.append((song, reason, original_line))
    return items, other_lines


def parse_failures(log_path):
    """Return [(song, reason, original_line)] of the retryable failures.

    Thin wrapper over read_failure_log for callers that only need the
    songs (e.g. counting them for the retry progress dialog). Playlist
    notices and unknown lines are skipped — they are not songs.
    """
    items, _ = read_failure_log(log_path)
    return items


def _pause(seconds, should_stop):
    """Sleep in small steps so a cancel request is noticed quickly."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        if should_stop and should_stop():
            return False
        time.sleep(min(0.5, remaining))


def _write_failure_log(log_path, still_failed, untried_lines, keep_lines=()):
    """Clear the failure log and put back only what should stay on it.

    Songs that failed again get a fresh timestamp; songs that were never
    retried (a cancelled run) keep their original line so nothing is lost;
    lines that are not about a song (playlist notices, unknown formats)
    stay exactly as they were.
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    lines = list(keep_lines) + list(untried_lines)
    for song, reason in still_failed:
        lines.append(f"[{timestamp}] {song}: {reason}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def retry_failures(playlist_id, folder, progress=None, should_stop=None, limit=None):
    """Retry every song on a playlist's failure log.

    Each song is searched on YouTube, downloaded into the playlist folder and
    registered in the playlist. Lines that are not about a song (playlist
    notices) are left untouched. Afterwards failed_songs.txt is cleared and
    rewritten with only the songs that failed again (songs that were never
    reached keep their old line). Returns a summary dict.
    """
    folder = Path(folder)
    log_path = failure_log_path(folder)
    items, keep_lines = read_failure_log(log_path)
    total = len(items)

    downloaded = []
    still_failed = []
    bot_blocked = False
    interrupted = False
    next_index = 0
    last_index = total if limit is None else min(limit, total)

    while next_index < last_index:
        song, _reason, _original = items[next_index]
        if should_stop and should_stop():
            interrupted = True
            break

        if progress:
            progress(next_index + 1, total, song)
        print(f"[retry] [{next_index + 1}/{total}] {song}", flush=True)

        failure_reason = None
        try:
            candidates = search_youtube(song, limit=SEARCH_LIMIT)
            if not candidates:
                failure_reason = "No YouTube match"
            else:
                candidate = candidates[0]
                # Short pause before downloading to stay under the bot radar.
                time.sleep(PRE_DOWNLOAD_PAUSE)
                file_path = download_audio_to_folder(
                    candidate["url"],
                    song,
                    str(folder),
                    use_yt_thumbnail=False,
                    skip_metadata_update=True,
                )
                if not file_path:
                    failure_reason = "Audio download failed"
                elif not add_downloaded_song_to_playlist(
                    song,
                    candidate["url"],
                    playlist_id,
                    file_path,
                    candidate.get("artist"),
                    candidate.get("thumbnail"),
                ):
                    failure_reason = "Could not register the file (path already used by another song)"
                else:
                    downloaded.append(song)
                    print(f"[retry]   OK -> {file_path}", flush=True)
        except BotBlockedError as error:
            # A bot-wall block is a cookie problem, not a broken song: every
            # download after this one would fail the same way, so stop the
            # run early and keep the rest on the list.
            failure_reason = f"Blocked by YouTube's bot check ({error})"
            bot_blocked = True
        except Exception as error:
            failure_reason = str(error)

        if failure_reason:
            print(f"[retry]   failed: {failure_reason}", flush=True)
            still_failed.append((song, failure_reason))

        next_index += 1

        if bot_blocked:
            interrupted = True
            break
        if next_index < last_index and not _pause(DELAY_SEC, should_stop):
            interrupted = True
            break

    untried = [items[i][2] for i in range(next_index, total)]
    _write_failure_log(log_path, still_failed, untried, keep_lines)

    summary_text = (
        f"Retry run at {datetime.now().isoformat(timespec='seconds')}\n"
        f"  Tried:        {len(downloaded) + len(still_failed)}\n"
        f"  Downloaded:   {len(downloaded)}\n"
        f"  Still failed: {len(still_failed)}\n"
        f"  Not retried:  {len(untried)}\n"
    )
    if still_failed:
        summary_text += "\nStill failed:\n" + "".join(
            f"  {song}: {reason}\n" for song, reason in still_failed
        )
    try:
        with (folder / "retry_log.txt").open("a", encoding="utf-8", newline="") as log:
            log.write(summary_text + "\n")
    except OSError as error:
        print(f"[retry] could not write the retry log: {error}", flush=True)

    return {
        "total": total,
        "downloaded": downloaded,
        "still_failed": still_failed,
        "not_retried": len(untried),
        "interrupted": interrupted,
        "bot_blocked": bot_blocked,
        "log_path": str(log_path),
    }


class RetryFailedWorker(QThread):
    """Retries a playlist's failed songs off the UI thread so the window
    never freezes during yt-dlp downloads (same pattern as SearchWorker)."""

    progress = Signal(int, int, str)   # current, total, song
    completed = Signal(object)        # summary dict from retry_failures
    failed = Signal(str)

    def __init__(self, playlist_id, folder, parent=None):
        super().__init__(parent)
        self.playlist_id = playlist_id
        self.folder = folder

    def run(self):
        try:
            summary = retry_failures(
                self.playlist_id,
                self.folder,
                progress=lambda current, total, song: self.progress.emit(current, total, song),
                should_stop=self.isInterruptionRequested,
            )
        except Exception as error:
            print(f"[retry] worker exception: {type(error).__name__}: {error}", flush=True)
            self.failed.emit(str(error))
            return
        self.completed.emit(summary)


def _find_playlist(name):
    for playlist_id, playlist_name, folder, _rest in get_playlists():
        if playlist_name == name:
            return playlist_id, folder
    raise RuntimeError(f"Playlist {name!r} not found")


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Retry failed song downloads.")
    parser.add_argument(
        "--playlist",
        default="Mixer",
        help="Playlist whose failure log should be retried (default: Mixer).",
    )
    parser.add_argument(
        "--browser",
        default=os.environ.get("YTDLP_BROWSER", ""),
        help=(
            "Browser to grab cookies from for yt-dlp "
            "(e.g. chrome, edge, firefox, opera-gx, zen)."
        ),
    )
    parser.add_argument(
        "--cookiefile",
        default=os.environ.get("YTDLP_COOKIEFILE", ""),
        help="Path to a Netscape-format cookie file exported from your browser.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only retry the first N songs from the failure log.",
    )
    args = parser.parse_args()

    if args.cookiefile:
        os.environ["YTDLP_COOKIEFILE"] = args.cookiefile
        print(f"[retry] Using cookie file: {args.cookiefile}", flush=True)
    elif args.browser:
        os.environ["YTDLP_BROWSER"] = args.browser
        print(f"[retry] Using browser cookies: {args.browser}", flush=True)

    playlist_id, folder = _find_playlist(args.playlist)
    print(f"Target playlist: {args.playlist} (id={playlist_id}, folder={folder})", flush=True)
    result = retry_failures(playlist_id, folder, limit=args.limit)
    print("\n" + "=" * 50)
    print(
        f"Downloaded:   {len(result['downloaded'])}\n"
        f"Still failed: {len(result['still_failed'])}\n"
        f"Not retried:  {result['not_retried']}"
    )

