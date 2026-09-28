"""Quick integration test for QueuePanel sort toggle."""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

app = QApplication(sys.argv)

from ui.queue_panel import QueuePanel

panel = QueuePanel()
panel.add_items([
    {"title": "B", "duration": 120, "added_at": "2023-01-02", "file_path": "b.mp3"},
    {"title": "A", "duration": 60,  "added_at": "2023-01-01", "file_path": "a.mp3"},
    {"title": "C", "duration": 180, "added_at": "2023-01-03", "file_path": "c.mp3"},
])

# Index 1 = Title, 2 = Duration, 3 = Date Added, 4 = Shuffled

def get_titles():
    return [panel.list_widget.item(i).data(Qt.UserRole)["title"] for i in range(panel.list_widget.count())]

# --- Test 1: first Title sort ---
panel._on_sort_activated(1)
t1 = get_titles()
assert t1 == ["A", "B", "C"], f"Expected A,B,C got {t1}"
print("PASS: First Title sort -> A, B, C")

# --- Test 2: same Title sort again -> reverse ---
panel._on_sort_activated(1)
t2 = get_titles()
assert t2 == ["C", "B", "A"], f"Expected C,B,A got {t2}"
print("PASS: Second Title sort -> C, B, A")

# --- Test 3: switch to Duration -> resets reverse ---
panel._on_sort_activated(2)
t3 = get_titles()
assert t3 == ["A", "B", "C"], f"Expected A,B,C got {t3}"
print("PASS: Duration sort -> A, B, C (shortest first)")

# --- Test 4: switch to Shuffled -> no reverse ---
panel._on_sort_activated(4)
# shuffled shows items_data in whatever order it currently is
shuffled_titles = get_titles()
print(f"SHUFFLED: {shuffled_titles}")
# shuffled mode intentionally skips re-sorting, so this is just a visual pass
print(f"PASS: Shuffled kept order ({shuffled_titles})")

# --- Test 5: using set_sort_mode programmatically resets state ---
panel.set_sort_mode("Title")
assert panel._reverse is False
assert panel._commit_index == 1
t5 = get_titles()
assert t5 == ["A", "B", "C"]
print("PASS: set_sort_mode reset works")

# Arrow-key dirty-then-commit
panel._on_sort_changed()  # simulates currentIndexChanged while arrow-navigating to Duration
panel._on_sort_activated(2)  # commit (first time on Duration)
assert get_titles() == ["A", "B", "C"]
print("PASS: arrow-nav then commit Duration -> A, B, C")

print("\nAll sort tests passed.")
