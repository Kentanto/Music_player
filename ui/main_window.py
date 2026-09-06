from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QGridLayout,
    QApplication, QAbstractButton, QLineEdit, QListWidget, QSlider, QComboBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeyEvent, QShortcut, QKeySequence



from .sidebar import Sidebar
from .search_panel import SearchPanel
from .queue_panel import QueuePanel
from .player_bar import PlayerBar
from .cover_widget import CoverWidget
from .fullscreen_player import FullscreenPlayer
from .eq_visualizer import EQVisualizer


class MainWindow(QMainWindow):
    """Main application window with sidebar, search, queue, player controls"""

    # Signals for business logic
    search_requested = Signal(str)
    play_pause_track = Signal()
    play_track_index = Signal(object)  # Play specific track by index
    queue_next_requested = Signal(object)
    add_to_playlist_requested = Signal(object)
    next_track = Signal()
    prev_track = Signal()
    shuffle_toggled = Signal(bool)
    load_playlists = Signal()
    add_to_playlist = Signal()
    import_list_requested = Signal()
    open_playlist_requested = Signal(int)
    volume_changed = Signal(int)
    seek_requested = Signal(float)
    seek_delta_requested = Signal(int)
    fullscreen_requested = Signal()
    track_selected = Signal(object)
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Music Engine")
        self.resize(1200, 800)

        self.init_ui()
        self._selected_queue_item = None
        self.connect_signals()

    def init_ui(self):
        """Initialize the main UI"""
        central = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # ===== TOP: Search and main content area =====
        top_layout = QHBoxLayout()

        # Left: Sidebar
        self.sidebar = Sidebar()
        top_layout.addWidget(self.sidebar, 0)

        # Middle: Search results / Queue
        middle_layout = QVBoxLayout()

        self.search_panel = SearchPanel()
        middle_layout.addWidget(self.search_panel, 0)

        # Center area with cover art and queue
        center_splitter = QSplitter(Qt.Horizontal)

        self.cover_widget = CoverWidget()
        center_splitter.addWidget(self.cover_widget)

        self.queue_panel = QueuePanel()
        center_splitter.addWidget(self.queue_panel)

        center_splitter.setSizes([300, 400])
        middle_layout.addWidget(center_splitter, 1)

        top_layout.addLayout(middle_layout, 1)

        main_layout.addLayout(top_layout, 1)

        # ===== BOTTOM: Player controls =====
        self.player_bar = PlayerBar()
        self.eq_visualizer = EQVisualizer(self.player_bar)
        self.player_bar.controls_layout.addWidget(self.eq_visualizer, 1)
        self.player_bar.controls_layout.addStretch()
        main_layout.addWidget(self.player_bar, 0)

        self.fullscreen_player = FullscreenPlayer(self)
        self._remote_highlighted = None
        self._remote_clear_timer = QTimer(self)
        self._remote_clear_timer.setSingleShot(True)
        self._remote_clear_timer.setInterval(8000)
        self._remote_clear_timer.timeout.connect(self.clear_remote_highlight)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

    def connect_signals(self):
        """Connect UI signals to business logic signals"""
        # Search
        self.search_panel.search_requested.connect(self.search_requested.emit)

        # Queue selection
        self.queue_panel.item_selected.connect(self.on_queue_item_selected)
        self.queue_panel.item_previewed.connect(self.on_queue_item_selected)
        self.queue_panel.item_double_clicked.connect(self.on_queue_item_double_clicked)
        self.queue_panel.queue_next_requested.connect(self.queue_next_requested.emit)
        self.queue_panel.add_to_playlist_requested.connect(self.add_to_playlist_requested.emit)

        # Player controls
        self.player_bar.play_pause_clicked.connect(self.play_pause_track.emit)
        self.player_bar.next_clicked.connect(self.next_track.emit)
        self.player_bar.prev_clicked.connect(self.prev_track.emit)
        self.player_bar.shuffle_toggled.connect(self.shuffle_toggled.emit)
        self.player_bar.volume_changed.connect(self.volume_changed.emit)
        self.player_bar.seek_requested.connect(self.seek_requested.emit)
        self.player_bar.fullscreen_requested.connect(self.fullscreen_requested.emit)
        self.fullscreen_player.seek_requested.connect(self.seek_requested.emit)

        # Sidebar
        self.sidebar.add_to_playlist_clicked.connect(self.add_to_playlist.emit)
        self.sidebar.import_list_clicked.connect(self.import_list_requested.emit)
        self.sidebar.playlists_clicked.connect(self.load_playlists.emit)

        fullscreen_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        fullscreen_shortcut.setContext(Qt.ApplicationShortcut)
        fullscreen_shortcut.activated.connect(self.fullscreen_requested.emit)

        QShortcut(QKeySequence(Qt.Key_MediaPlay), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaPause), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaNext), self).activated.connect(self.next_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaPrevious), self).activated.connect(self.prev_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaTogglePlayPause), self).activated.connect(self.play_pause_track.emit)

    # ───────────────────────── Navigation grid ─────────────────────────

    def _navigation_rows(self):
        """Return the 2D grid of focusable widgets for arrow-key/remote nav."""
        if self.fullscreen_player.isVisible():
            if getattr(self.fullscreen_player, "_idle_mode", False):
                return [[self.fullscreen_player.eq_seek_slider]]
            return [
                [self.fullscreen_player.seek_slider],
                [
                    self.fullscreen_player.prev_button,
                    self.fullscreen_player.play_pause_button,
                    self.fullscreen_player.next_button,
                ],
                [self.fullscreen_player.volume_slider],
            ]

        return [
            [self.search_panel.search_bar],
            [
                self.sidebar.playlists_btn,
                self.sidebar.add_to_playlist_btn,
                self.sidebar.import_list_btn,
            ],
            [self.queue_panel.filter_input, self.queue_panel.sort_combo],
            [self.queue_panel.list_widget],
            [self.player_bar.seek_slider],
            [
                self.player_bar.prev_btn,
                self.player_bar.play_pause_btn,
                self.player_bar.next_btn,
                self.player_bar.shuffle_btn,
                self.player_bar.fullscreen_btn,
            ],
            [self.player_bar.volume_slider],
        ]

    def _all_navigation_targets(self):
        return [target for row in self._navigation_rows() for target in row]

    # ──────────────────────── Keyboard overrides ───────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        # Route arrow keys to the same grid engine the CEC remote uses
        if key == Qt.Key_Up:
            self.navigate("up")
            return
        if key == Qt.Key_Down:
            self.navigate("down")
            return
        if key == Qt.Key_Left:
            self.navigate("left")
            return
        if key == Qt.Key_Right:
            self.navigate("right")
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.activate_highlighted()
            return
        if key == Qt.Key_Escape:
            if self.fullscreen_player.isVisible():
                self.fullscreen_player.close()
                return
            if self._remote_highlighted is not None:
                self.clear_remote_highlight()
                return
        super().keyPressEvent(event)

    # ────────────────────── Shared navigation engine ────────────────────

    def navigate_remote(self, direction):
        """Entry point used by the CEC remote thread."""
        self.navigate(direction)

    def navigate(self, direction):
        rows = self._navigation_rows()
        targets = self._all_navigation_targets()
        if not targets:
            print(f"[NAV] {direction}: no navigation targets", flush=True)
            return

        current = self._remote_highlighted

        # ── Special: up/down inside QListWidget scrolls items ──
        if isinstance(current, QListWidget) and direction in {"up", "down"}:
            row = current.currentRow()
            next_row = row + (1 if direction == "down" else -1)
            if 0 <= next_row < current.count():
                current.setCurrentRow(next_row)
                current.scrollToItem(current.currentItem())
                print(
                    f"[NAV] {direction}: queue item {next_row + 1}/{current.count()}",
                    flush=True,
                )
                self._refresh_widget_style(current)
                self._remote_clear_timer.start()
            return

        # ── Special: left/right on a slider adjusts value ──
        if current in targets and isinstance(current, QSlider) and direction in {"left", "right"}:
            if current is self.player_bar.volume_slider or current is self.fullscreen_player.volume_slider:
                delta = -5 if direction == "left" else 5
                new_val = max(0, min(100, current.value() + delta))
                current.setValue(new_val)
                print(f"[NAV] {direction}: volume {new_val}", flush=True)
            else:
                delta = -5 if direction == "left" else 5
                print(
                    f"[NAV] {direction}: {self._target_name(current)} ({delta:+d}s)",
                    flush=True,
                )
                self.seek_delta_requested.emit(delta)
            self._remote_clear_timer.start()
            return

        # ── Special: left/right inside QComboBox changes index ──
        if current in targets and isinstance(current, QComboBox) and direction in {"left", "right"}:
            idx = current.currentIndex()
            new_idx = idx + (1 if direction == "right" else -1)
            if 0 <= new_idx < current.count():
                current.setCurrentIndex(new_idx)
                print(f"[NAV] {direction}: {self._target_name(current)} idx {new_idx}", flush=True)
            self._remote_clear_timer.start()
            return

        # ── Grid movement ──
        if current not in targets:
            # Nothing highlighted yet — pick a sensible starting point
            if direction in {"down", "right"}:
                row_index, target_index = 0, 0
            else:
                row_index = len(rows) - 1
                target_index = len(rows[row_index]) - 1
        else:
            row_index = next(i for i, r in enumerate(rows) if current in r)
            current_index = rows[row_index].index(current)
            if direction in {"left", "right"}:
                target_index = (current_index + (1 if direction == "right" else -1)) % len(rows[row_index])
            else:  # up / down
                target_row = row_index + (1 if direction == "down" else -1)
                target_row %= len(rows)
                target_index = min(current_index, len(rows[target_row]) - 1)
                row_index = target_row

        target = rows[row_index][target_index]
        print(
            f"[NAV] {direction}: {self._target_name(current)} -> "
            f"{self._target_name(target)}",
            flush=True,
        )
        self._set_highlight(target)

    def activate_remote_target(self):
        """Entry point used by the CEC remote thread (Select button)."""
        self.activate_highlighted()

    def activate_highlighted(self):
        target = self._remote_highlighted
        if target is None:
            # If nothing is highlighted, auto-highlight the queue and select its current item
            target = self.queue_panel.list_widget
            if target.count():
                self._set_highlight(target)
            print("[NAV] select: auto-focused queue", flush=True)
            return

        print(f"[NAV] select: {self._target_name(target)}", flush=True)

        if isinstance(target, QListWidget):
            item = target.currentItem()
            if item:
                item.setSelected(True)
                target.itemActivated.emit(item)
        elif isinstance(target, QAbstractButton):
            target.click()
        elif isinstance(target, QLineEdit):
            target.setFocus(Qt.OtherFocusReason)
            target.selectAll()
        elif isinstance(target, QSlider):
            target.setFocus(Qt.OtherFocusReason)
        elif isinstance(target, QComboBox):
            target.showPopup()

        self._remote_clear_timer.start()

    # ──────────────────────── Highlight helpers ────────────────────────

    def _set_highlight(self, target):
        if self._remote_highlighted is not None:
            self._remote_highlighted.setProperty("remoteHighlight", False)
            self._refresh_widget_style(self._remote_highlighted)

        self._remote_highlighted = target
        target.setProperty("remoteHighlight", True)
        target.setFocus(Qt.OtherFocusReason)

        # When focusing the queue, jump to the currently-playing track if possible
        if target is self.queue_panel.list_widget and target.count():
            current_row = target.currentRow()
            if current_row < 0:
                # Try to find the highlighted/playing item first
                found = -1
                for i in range(target.count()):
                    item = target.item(i)
                    if item and item.font().bold():
                        found = i
                        break
                target.setCurrentRow(found if found >= 0 else 0)
            target.scrollToItem(target.currentItem())

        self._refresh_widget_style(target)
        self._remote_clear_timer.start()

    @staticmethod
    def _target_name(target):
        if target is None:
            return "none"
        if isinstance(target, QListWidget):
            item = target.currentItem()
            suffix = f"={item.text()}" if item else ""
            return f"queue[{target.currentRow()}]{suffix}"
        if isinstance(target, QAbstractButton):
            return f"button[{target.text() or target.objectName()}]"
        if isinstance(target, QLineEdit):
            return "search" if "search" in (target.objectName() or "").lower() else "filter"
        if isinstance(target, QComboBox):
            return f"combo[{target.currentText() or target.objectName()}]"
        if isinstance(target, QSlider):
            return f"slider[{target.objectName() or 'value'}]"
        return target.objectName() or target.__class__.__name__

    def clear_remote_highlight(self):
        if self._remote_highlighted is None:
            return
        self._remote_highlighted.setProperty("remoteHighlight", False)
        self._refresh_widget_style(self._remote_highlighted)
        self._remote_highlighted.clearFocus()
        self._remote_highlighted = None

    @staticmethod
    def _refresh_widget_style(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    # ───────────────────────── Standard methods ────────────────────────

    def on_queue_item_selected(self, item):
        """Handle queue item selection"""
        self._selected_queue_item = item
        self.track_selected.emit(item)

    def get_selected_queue_item(self):
        return self._selected_queue_item or self.queue_panel.get_current_item()

    def on_queue_item_double_clicked(self, item):
        if item.get("type") == "playlist":
            self.open_playlist_requested.emit(item.get("playlist_id"))
        else:
            self.play_track_index.emit(item)

    def display_results(self, results, preserve_order=False, current_item_source=None):
        """Display search results in queue panel"""
        self._selected_queue_item = None
        self.queue_panel.add_items(results, preserve_order=preserve_order, current_item_source=current_item_source)

    def display_library(self, songs, preserve_order=False, current_item_source=None):
        """Display library songs"""
        self._selected_queue_item = None
        results = []
        for song in songs:
            if len(song) == 2:
                title, url = song
                results.append({"title": title, "url": url, "type": "track"})
            elif len(song) >= 3:
                title, url, file_path = song[:3]
                results.append({"title": title, "url": url, "file_path": file_path, "type": "track"})
        self.queue_panel.add_items(results, preserve_order=preserve_order, current_item_source=current_item_source)

    def display_playlists(self, playlists, preserve_order=False, current_item_source=None):
        """Display playlist list in the queue panel"""
        self._selected_queue_item = None
        self.queue_panel.add_items(playlists, preserve_order=preserve_order, current_item_source=current_item_source)

    def get_current_queue_urls(self):
        """Get all URLs in current queue"""
        return self.queue_panel.get_all_urls()

    def get_current_track_index(self):
        """Get currently selected track index"""
        return self.queue_panel.get_current_index()  # LEGACY ONLY

    def update_player_time(self, current, total):
        """Update player time display"""
        self.player_bar.set_time_label(current, total)

    def set_volume(self, value):
        """Update volume slider"""
        self.player_bar.set_volume(value)
