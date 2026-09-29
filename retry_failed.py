"""One-off retry script for songs that previously failed during import.

Run: python retry_failed.py [--browser chrome|firefox|edge| brave] [--limit N]
Delete this file when you're done.
"""

import sys
import re
import os
import time
import argparse
from pathlib import Path
from datetime import datetime

# Parse CLI args early so we can set cookie env vars before importing anything that reads them.
_parser = argparse.ArgumentParser(description="Retry failed song downloads.")
_parser.add_argument(
    "--browser",
    default=os.environ.get("YTDLP_BROWSER", ""),
    help="Browser to grab cookies from for yt-dlp (e.g. chrome, edge, firefox).",
)
_parser.add_argument(
    "--cookiefile",
    default=os.environ.get("YTDLP_COOKIEFILE", ""),
    help="Path to a Netscape-format cookie file exported from your browser.",
)
_parser.add_argument(
    "--limit",
    type=int,
    default=None,
    help="Only retry the first N songs from the failure log.",
)
_args = _parser.parse_args()

if _args.cookiefile:
    os.environ["YTDLP_COOKIEFILE"] = _args.cookiefile
    print(f"[retry] Using cookie file: {_args.cookiefile}", flush=True)
elif _args.browser:
    os.environ["YTDLP_BROWSER"] = _args.browser
    print(f"[retry] Using browser cookies: {_args.browser}", flush=True)

from spotify_download import get_best_youtube_result
from db import (
    add_downloaded_song_to_playlist,
    download_audio_to_folder,
    get_playlists,
)

FAILURE_LOG = Path("playlists/Mixer/failed_songs.txt")
RETRY_LOG = FAILURE_LOG.with_name("retry_log.txt")
PLAYLIST_NAME = "Mixer"
DELAY_SEC = 12  # pause between songs to avoid bot wall


def _find_playlist():
    for pid, name, folder, _ in get_playlists():
        if name == PLAYLIST_NAME:
            return pid, folder
    raise RuntimeError(f"Playlist {PLAYLIST_NAME!r} not found")


def _parse_failures(path=FAILURE_LOG):
    """Yield (song_line, reason) from the failure log."""
    if not path.exists():
        print(f"No failure log found at {path}")
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            # Format: [2026-09-29T12:16:50] Black Out - Azari: Audio download failed
            m = re.match(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] (.+?): (.+)", line)
            if not m:
                continue
            yield m.group(1), m.group(2)


def retry_all(limit=None):
    playlist_id, folder = _find_playlist()
    print(f"Target playlist: {PLAYLIST_NAME} (id={playlist_id}, folder={folder})")

    if not os.environ.get("YTDLP_BROWSER"):
        print(
            "\nWARNING: YTDLP_BROWSER is not set.  yt-dlp will probably hit the bot wall.\n"
            "Pass --browser, e.g.:\n"
            "  python retry_failed.py --browser chrome\n"
            "  python retry_failed.py --browser edge --limit 5\n"
        )

    reloaded = []
    already_existed = []
    still_failed = []

    items = list(_parse_failures())
    if limit:
        items = items[:limit]

    for idx, (song_line, reason) in enumerate(items, start=1):
        print(f"\n[retry {idx}/{len(items)}] {song_line}")
        candidate = get_best_youtube_result(song_line, "")
        if not candidate:
            reason_out = "No YouTube match"
            print(f"[retry]   {reason_out}")
            still_failed.append((song_line, reason_out))
            time.sleep(DELAY_SEC)
            continue

        url = candidate["url"]
        yt_artist = candidate.get("uploader") or "Unknown"
        thumbnail = candidate.get("thumbnail")

        file_path = download_audio_to_folder(
            url, song_line, folder,
            use_yt_thumbnail=False,
            skip_metadata_update=True,
        )
        if not file_path:
            reason_out = "Audio download failed"
            print(f"[retry]   {reason_out}")
            still_failed.append((song_line, reason_out))
            time.sleep(DELAY_SEC)
            continue

        add_downloaded_song_to_playlist(
            song_line, url, playlist_id, file_path, yt_artist, thumbnail
        )
        if Path(file_path).exists():
            print(f"[retry]   OK -> {file_path}")
            reloaded.append(song_line)
        else:
            print(f"[retry]   already existed")
            already_existed.append(song_line)

        # Cool-down between songs to avoid bot wall
        if idx < len(items):
            time.sleep(DELAY_SEC)

    # Write a quick summary
    summary = (
        f"Retry run at {datetime.now().isoformat(timespec='seconds')}\n"
        f"  Processed:    {len(items)}\n"
        f"  Success:      {len(reloaded)}\n"
        f"  Already had:  {len(already_existed)}\n"
        f"  Still failed: {len(still_failed)}\n"
    )
    if still_failed:
        summary += "\nStill failed:\n"
        for song, reason in still_failed:
            summary += f"  {song}: {reason}\n"

    with RETRY_LOG.open("a", encoding="utf-8", newline="") as f:
        f.write(summary + "\n")

    print("\n" + "=" * 50)
    print(summary)
    print(f"Wrote details to {RETRY_LOG}")


if __name__ == "__main__":
    retry_all(limit=_args.limit)
