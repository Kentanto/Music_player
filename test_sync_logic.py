"""Headless check of the shuffle / queue sync logic (no audio, no images).

Runs two Player instances as if they were two synced machines and verifies
that they end up with byte-for-byte the same play order in every scenario:
- shuffle toggled on one machine (seed + base queue adoption on the other),
- shuffle toggled off again,
- a song queued as "next" on one machine and applied on the other,
- a full play-order adoption after a track change.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

from player import Player

songs = [f"/music/song_{i}.mp3" for i in range(10)]
failures = []


def check(name, a, b):
    if a == b:
        print(f"PASS: {name}")
    else:
        failures.append(name)
        print(f"FAIL: {name}\n  A: {a}\n  B: {b}")


# --- Scenario 1: shuffle on machine A propagates the exact order to B ---
a = Player()
b = Player()

a.set_queue(songs, current_item=songs[0])
a.toggle_shuffle(True)

b.apply_synced_shuffle(
    a.shuffle_enabled,
    a._shuffle_seed,
    a._base_queue,
    current_item=a._current_item,
    next_sources=list(a._next_sources),
)
check("shuffle on -> same queue order", a.queue, b.queue)
check("shuffle on -> same seed", a._shuffle_seed, b._shuffle_seed)
check("shuffle on -> same index", a.index, b.index)

# Advancing must pick the same track on both machines.
a._advance()
b._advance()
check("advance while shuffled -> same track", a.queue[a.index], b.queue[b.index])

# --- Scenario 2: shuffle turned off propagates the linear order ---
a.toggle_shuffle(False)
b.apply_synced_shuffle(
    a.shuffle_enabled,
    a._shuffle_seed,
    a._base_queue,
    current_item=a._current_item,
    next_sources=list(a._next_sources),
)
check("shuffle off -> same queue order", a.queue, b.queue)
check("shuffle off -> same index", a.index, b.index)

# --- Scenario 3: queue_next on A lands the same on B ---
a.queue_next("/music/inserted.mp3")
# B adopts it the way the sync handler does (queue_next then restore):
b.queue_next("/music/inserted.mp3")
check("queue_next -> same queue order", a.queue, b.queue)
a._advance()
b._advance()
check("advance over queued-next -> same track", a.queue[a.index], b.queue[b.index])

# --- Scenario 4: B had a completely different queue, then adopts A's play order ---
b.queue = ["/other/x1.mp3", "/other/x2.mp3"]
b.index = 0
b._current_item = b.queue[0]
b.adopt_synced_queue(a.queue, a.index, list(a._next_sources))
check("adopt play order -> same queue", a.queue, b.queue)
check("adopt play order -> same index", a.index, b.index)
a._advance()
b._advance()
check("advance after adoption -> same track", a.queue[a.index], b.queue[b.index])

# --- Scenario 5: apply_synced_shuffle keeps the current song first even mid-queue ---
c = Player()
d = Player()
c.set_queue(songs, current_item=songs[4])
c.toggle_shuffle(True)
d.set_queue(songs, current_item=songs[0])  # d was anchored elsewhere
d.apply_synced_shuffle(
    c.shuffle_enabled,
    c._shuffle_seed,
    c._base_queue,
    current_item=c._current_item,
    next_sources=list(c._next_sources),
)
check("mid-queue shuffle -> same order", c.queue, d.queue)
check("mid-queue shuffle -> current first", c.queue[0], d.queue[0])

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("ALL SYNC LOGIC CHECKS PASSED")
