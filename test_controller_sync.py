"""Headless check of the controller-level sync helpers in main.py.

No window is built; the controller methods are called on a stub that
provides just the attributes they use. Verifies the full round trip a
broadcast makes: sender builds {file, url} identities -> JSON round trip ->
receiver maps them back to playable sources and adopts shuffle/queue state.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

import main as main_module
from player import Player

Controller = main_module.MusicAppController
failures = []


def check(name, ok, detail=""):
    if ok:
        print(f"PASS: {name}")
    else:
        failures.append(name)
        print(f"FAIL: {name} {detail}")


from db import to_portable_path, DATA_DIR

scratch = Path(tempfile.mkdtemp(prefix="sync_test_"))
local_files = []
for i in range(6):
    f = scratch / f"song_{i}.mp3"
    f.write_bytes(b"x")
    local_files.append(str(f))

# --- sender side -----------------------------------------------------------
sender = SimpleNamespace()
sender._sync_identity_cache = {}
sender.current_results = [
    {"type": "track", "title": f"Song {i}", "url": f"https://example.com/{i}",
     "file_path": local_files[i], "artist": f"Artist {i}"}
    for i in range(6)
]
sender.window = SimpleNamespace(
    queue_panel=SimpleNamespace(
        items_data=list(sender.current_results), master_items=[]
    )
)
sender.player = Player()
sender.player.set_queue(local_files, current_item=local_files[0])
sender.player.toggle_shuffle(True)
# Bind the real pool helper (it only reads the stub attributes above).
sender._sync_item_pools = Controller._sync_item_pools.__get__(sender)

identities = [Controller._sync_source_identity(sender, s) for s in sender.player._base_queue]
check("identity has portable file", all(p["file"] for p in identities), str(identities[:1]))
check("identity has stream url", all(p["url"] for p in identities))

payload = {
    "enabled": sender.player.shuffle_enabled,
    "seed": sender.player._shuffle_seed,
    "current": Controller._sync_source_identity(sender, sender.player._current_item),
    "queue": identities,
    "next_sources": [
        Controller._sync_source_identity(sender, s) for s in sender.player._next_sources
    ],
}
wire = json.loads(json.dumps(payload))  # what actually goes over TCP

# --- receiver side ---------------------------------------------------------
receiver = SimpleNamespace()
receiver._sync_identity_cache = {}
receiver.current_results = []
receiver.window = SimpleNamespace(
    queue_panel=SimpleNamespace(items_data=[], master_items=[])
)
receiver.player = Player()
receiver.active_queue_urls = []
receiver.refreshed = 0


def fake_refresh(*a, **k):
    receiver.refreshed += 1


def fake_publish(enabled):
    receiver.published = enabled


receiver._refresh_queue_display = fake_refresh
receiver._publish_shuffle_state = fake_publish
# Bind the real helpers (they only read the stub attributes above).
receiver._sync_item_pools = Controller._sync_item_pools.__get__(receiver)
receiver._sync_source_from_identity = Controller._sync_source_from_identity.__get__(receiver)

# Machine B queues something else entirely first (the user's drift scenario).
other = [str(scratch / f"other_{i}.mp3") for i in range(3)]
for p in other:
    Path(p).write_bytes(b"x")
receiver.player.set_queue(other, current_item=other[0])

# Apply the sender's shuffle broadcast the way _on_sync_remote_command does.
Controller._sync_apply_shuffle(receiver, wire)

check("shuffle adopted -> same order",
      receiver.player.queue == sender.player.queue,
      f"\n  sender: {sender.player.queue}\n  receiver: {receiver.player.queue}")
check("shuffle adopted -> same seed",
      receiver.player._shuffle_seed == sender.player._shuffle_seed)
check("shuffle adopted -> shuffle flag on", receiver.player.shuffle_enabled)
check("shuffle UI republished", receiver.published is True)

# Advancing both must land on the same track.
sender.player._advance()
receiver.player._advance()
check("advance after remote shuffle -> same track",
      sender.player.queue[sender.player.index] == receiver.player.queue[receiver.player.index])

# --- queue_next round trip --------------------------------------------------
queued = {"file": to_portable_path(local_files[3]), "url": "https://example.com/3",
          "title": "Song 3", "artist": "Artist 3"}
queued_wire = json.loads(json.dumps(queued))
Controller._sync_apply_queue_next(receiver, queued_wire)
sender.player.queue_next(local_files[3])
check("queue_next applied -> same order",
      receiver.player.queue == sender.player.queue,
      f"\n  sender: {sender.player.queue}\n  receiver: {receiver.player.queue}")

# --- play broadcast with queue adoption -------------------------------------
sender.player._advance()
play_payload = {
    "file": to_portable_path(sender.player.queue[sender.player.index]),
    "url": None,
    "title": "t",
    "position_ms": 0,
    "queue": [
        Controller._sync_source_identity(sender, s) for s in sender.player.queue
    ],
    "queue_index": sender.player.index,
    "next_sources": [],
}
play_wire = json.loads(json.dumps(play_payload))
adopted = Controller._sync_adopt_queue_payload(receiver, play_wire)
check("play queue adopted", adopted)
check("play queue adopted -> same order",
      receiver.player.queue == sender.player.queue)
check("play queue adopted -> same index",
      receiver.player.index == sender.player.index)

# An unknown song (missing here, no URL) is skipped, not fatal.
broken_payload = {
    "queue": [
        {"file": "playlists/does_not_exist.mp3", "url": None},
        {"file": None, "url": "https://example.com/9"},
    ],
    "queue_index": 0,
    "next_sources": [],
}
receiver2 = SimpleNamespace()
receiver2._sync_identity_cache = {}
receiver2.current_results = []
receiver2.window = SimpleNamespace(queue_panel=SimpleNamespace(items_data=[], master_items=[]))
receiver2.player = Player()
receiver2.player.set_queue(["https://example.com/keep.mp3"])
receiver2._refresh_queue_display = fake_refresh
receiver2._sync_item_pools = Controller._sync_item_pools.__get__(receiver2)
receiver2._sync_source_from_identity = Controller._sync_source_from_identity.__get__(receiver2)
Controller._sync_adopt_queue_payload(receiver2, broken_payload)
check("unmappable song skipped, url song kept",
      receiver2.player.queue == ["https://example.com/9"],
      str(receiver2.player.queue))

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("ALL CONTROLLER SYNC CHECKS PASSED")
