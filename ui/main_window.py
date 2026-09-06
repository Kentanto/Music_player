from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QGridLayout,
    QApplication, QAbstractButton, QLineEdit, QListWidget, QSlider, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QTimer, QEvent, QRect, QPoint
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
    play_track_index = Signal(object)
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
        self._install_nav_filters()

    def init_ui(self):
        central = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        top_layout = QHBoxLayout()
        self.sidebar = Sidebar()
        top_layout.addWidget(self.sidebar, 0)

        middle_layout = QVBoxLayout()
        self.search_panel = SearchPanel()
        middle_layout.addWidget(self.search_panel, 0)

        center_splitter = QSplitter(Qt.Horizontal)
        self.cover_widget = CoverWidget()
        center_splitter.addWidget(self.cover_widget)
        self.queue_panel = QueuePanel()
        center_splitter.addWidget(self.queue_panel)
        center_splitter.setSizes([300, 400])
        middle_layout.addWidget(center_splitter, 1)
        top_layout.addLayout(middle_layout, 1)
        main_layout.addLayout(top_layout, 1)

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
        self.search_panel.search_requested.connect(self.search_requested.emit)
        self.queue_panel.item_selected.connect(self.on_queue_item_selected)
        self.queue_panel.item_previewed.connect(self.on_queue_item_selected)
        self.queue_panel.item_double_clicked.connect(self.on_queue_item_double_clicked)
        self.queue_panel.queue_next_requested.connect(self.queue_next_requested.emit)
        self.queue_panel.add_to_playlist_requested.connect(self.add_to_playlist_requested.emit)
        self.player_bar.play_pause_clicked.connect(self.play_pause_track.emit)
        self.player_bar.next_clicked.connect(self.next_track.emit)
        self.player_bar.prev_clicked.connect(self.prev_track.emit)
        self.player_bar.shuffle_toggled.connect(self.shuffle_toggled.emit)
        self.player_bar.volume_changed.connect(self.volume_changed.emit)
        self.player_bar.seek_requested.connect(self.seek_requested.emit)
        self.player_bar.fullscreen_requested.connect(self.fullscreen_requested.emit)
        self.fullscreen_player.seek_requested.connect(self.seek_requested.emit)
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

    # ───────────────────── Event filter installation ─────────────────────

    def _install_nav_filters(self):
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            target = self._find_nav_target_for_widget(watched)
            if target and target is not self._remote_highlighted:
                if self._remote_highlighted is not None and not self._is_fullscreen_nav():
                    self._remote_highlighted.setProperty("remoteHighlight", False)
                    self._refresh_widget_style(self._remote_highlighted)
                self._remote_highlighted = target
                if not self._is_fullscreen_nav():
                    target.setProperty("remoteHighlight", True)
                    self._refresh_widget_style(target)
                self._remote_clear_timer.start()
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            target = self._find_nav_target_for_widget(watched)
            if target is None:
                return super().eventFilter(watched, event)

            # ── Return / Space = activate (always) ──
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                if isinstance(target, QLineEdit) and key == Qt.Key_Space:
                    # Let space type in search/filter fields
                    return super().eventFilter(watched, event)
                self.activate_highlighted()
                return True

            # ── QLineEdit: only navigate when cursor is at edge ──
            if isinstance(target, QLineEdit):
                if key == Qt.Key_Left:
                    if target.cursorPosition() == 0:
                        self.navigate("left")
                        return True
                    return super().eventFilter(watched, event)
                if key == Qt.Key_Right:
                    if target.cursorPosition() >= len(target.text()):
                        self.navigate("right")
                        return True
                    return super().eventFilter(watched, event)
                if key in (Qt.Key_Up, Qt.Key_Down):
                    self.navigate("up" if key == Qt.Key_Up else "down")
                    return True

            # ── QComboBox: only navigate when not open ──
            if isinstance(target, QComboBox):
                if target.view().isVisible():
                    # Combo popup is open — only intercept Return/Space, not arrows
                    return super().eventFilter(watched, event)
                if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                    self.navigate(
                        "left" if key == Qt.Key_Left
                        else "right" if key == Qt.Key_Right
                        else "up" if key == Qt.Key_Up
                        else "down"
                    )
                    return True

            # ── Everything else: always navigate ──
            if key == Qt.Key_Up:
                self.navigate("up")
                return True
            if key == Qt.Key_Down:
                self.navigate("down")
                return True
            if key == Qt.Key_Left:
                self.navigate("left")
                return True
            if key == Qt.Key_Right:
                self.navigate("right")
                return True

        return super().eventFilter(watched, event)

    def _find_nav_target_for_widget(self, widget):
        if not isinstance(widget, QWidget):
            return None
        for target in self._navigation_targets():
            if widget is target or target.isAncestorOf(widget):
                return target
        return None

    # ───────────────────── Navigation target registry ────────────────────

    def _is_fullscreen_nav(self):
        return self.fullscreen_player.isVisible() and not getattr(self.fullscreen_player, "_idle_mode", False)

    def _navigation_targets(self):
        if self.fullscreen_player.isVisible():
            if getattr(self.fullscreen_player, "_idle_mode", False):
                return [self.fullscreen_player.eq_seek_slider]
            return [
                self.fullscreen_player.seek_slider,
                self.fullscreen_player.prev_button,
                self.fullscreen_player.play_pause_button,
                self.fullscreen_player.next_button,
                self.fullscreen_player.volume_slider,
            ]
        return [
            self.search_panel.search_bar,
            self.sidebar.playlists_btn,
            self.sidebar.add_to_playlist_btn,
            self.sidebar.import_list_btn,
            self.cover_widget,
            self.queue_panel.filter_input,
            self.queue_panel.sort_combo,
            self.queue_panel.list_widget,
            self.player_bar.seek_slider,
            self.player_bar.prev_btn,
            self.player_bar.play_pause_btn,
            self.player_bar.next_btn,
            self.player_bar.shuffle_btn,
            self.player_bar.fullscreen_btn,
            self.player_bar.volume_slider,
        ]

    def _fullscreen_nav_rows(self):
        """Explicit row grid for fullscreen — avoids broken spatial math on overlays."""
        return [
            [self.fullscreen_player.seek_slider],
            [
                self.fullscreen_player.prev_button,
                self.fullscreen_player.play_pause_button,
                self.fullscreen_player.next_button,
            ],
            [self.fullscreen_player.volume_slider],
        ]

    # ────────────────────── Keyboard overrides ───────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
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
        if key == Qt.Key_Space:
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

    # ───────────────────── Shared navigation engine ──────────────────────

    def navigate_remote(self, direction):
        self.navigate(direction)

    def navigate(self, direction):
        targets = self._navigation_targets()
        if not targets:
            return

        # Resolve the effective "current" widget
        current = self._remote_highlighted
        if current not in targets:
            focused = QApplication.focusWidget()
            if focused:
                for t in targets:
                    if focused is t or t.isAncestorOf(focused):
                        current = t
                        break

        if current not in targets:
            # Nothing active yet — start on the queue (most useful default)
            default = self.queue_panel.list_widget
            if default.count():
                print(f"[NAV] {direction}: auto-start -> queue", flush=True)
                self._set_highlight(default)
            else:
                print(f"[NAV] {direction}: auto-start -> {self._target_name(targets[0])}", flush=True)
                self._set_highlight(targets[0])
            return

        # ── Queue: up / down scrolls items, at edges leaves the widget ──
        if isinstance(current, QListWidget) and direction in {"up", "down"}:
            row = current.currentRow()
            if row < 0 and current.count():
                current.setCurrentRow(0)
                self._remote_clear_timer.start()
                return
            delta = 1 if direction == "down" else -1
            next_row = row + delta
            if 0 <= next_row < current.count():
                current.setCurrentRow(next_row)
                current.scrollToItem(current.currentItem())
                self._remote_clear_timer.start()
                return

        # ── Slider: left / right adjusts value ──
        if isinstance(current, QSlider) and direction in {"left", "right"}:
            is_vol = current is self.player_bar.volume_slider or current is self.fullscreen_player.volume_slider
            delta = -5 if direction == "left" else 5
            if is_vol:
                new_val = max(0, min(100, current.value() + delta))
                current.setValue(new_val)
                print(f"[NAV] {direction}: volume {new_val}", flush=True)
            else:
                print(f"[NAV] {direction}: seek {delta:+d}s", flush=True)
                self.seek_delta_requested.emit(delta)
            self._remote_clear_timer.start()
            return

        # ── ComboBox: left / right changes index ──
        if isinstance(current, QComboBox) and direction in {"left", "right"}:
            idx = current.currentIndex()
            new_idx = idx + (1 if direction == "right" else -1)
            if 0 <= new_idx < current.count():
                current.setCurrentIndex(new_idx)
            self._remote_clear_timer.start()
            return

        # ── Fullscreen: use explicit row grid (spatial math breaks on overlays) ──
        if self._is_fullscreen_nav():
            target = self._fullscreen_grid_navigate(current, direction)
            if target:
                print(
                    f"[NAV] {direction}: {self._target_name(current)} -> {self._target_name(target)}",
                    flush=True,
                )
                self._set_highlight(target)
            return

        # ── Normal mode: spatial navigation ──
        target = self._find_target_in_direction(current, direction)
        if target is None:
            if direction in {"left", "right"}:
                target = self._row_wrap_fallback(current, direction)
            else:
                target = self._column_wrap_fallback(current, direction)
        if target:
            print(
                f"[NAV] {direction}: {self._target_name(current)} -> {self._target_name(target)}",
                flush=True,
            )
            self._set_highlight(target)

    def _fullscreen_grid_navigate(self, current, direction):
        """Predictable row/column grid for fullscreen controls."""
        rows = self._fullscreen_nav_rows()
        all_targets = [t for row in rows for t in row]
        if current not in all_targets:
            return rows[1][1] if rows[1] else None  # default to play button

        row_idx = next(i for i, row in enumerate(rows) if current in row)
        col_idx = rows[row_idx].index(current)

        if direction in {"left", "right"}:
            row = rows[row_idx]
            delta = -1 if direction == "left" else 1
            new_col = (col_idx + delta) % len(row)
            return row[new_col]

        if direction == "up":
            new_row = (row_idx - 1) % len(rows)
            new_col = min(col_idx, len(rows[new_row]) - 1)
            return rows[new_row][new_col]

        if direction == "down":
            new_row = (row_idx + 1) % len(rows)
            new_col = min(col_idx, len(rows[new_row]) - 1)
            return rows[new_row][new_col]

        return None

    # ───────────────────── Spatial navigation core ───────────────────────

    def _widget_rect_global(self, widget):
        geo = widget.geometry()
        top_left = widget.mapToGlobal(geo.topLeft())
        bottom_right = widget.mapToGlobal(geo.bottomRight())
        return QRect(top_left, bottom_right)

    def _find_target_in_direction(self, current, direction):
        """Find closest widget in direction using overlap-aware rectangle distance."""
        current_rect = self._widget_rect_global(current)
        candidates = []

        for t in self._navigation_targets():
            if t is current or not t.isVisible():
                continue
            t_rect = self._widget_rect_global(t)

            if direction == "right":
                if t_rect.center().x() <= current_rect.center().x() + 5:
                    continue
                score = self._distance_right(current_rect, t_rect)
            elif direction == "left":
                if t_rect.center().x() >= current_rect.center().x() - 5:
                    continue
                score = self._distance_right(t_rect, current_rect)
            elif direction == "down":
                if t_rect.center().y() <= current_rect.center().y() + 5:
                    continue
                score = self._distance_down(current_rect, t_rect)
            elif direction == "up":
                if t_rect.center().y() >= current_rect.center().y() - 5:
                    continue
                score = self._distance_down(t_rect, current_rect)
            else:
                continue

            candidates.append((t, score))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    @staticmethod
    def _distance_right(a_rect, b_rect):
        """Score from a to b when b is to the right of a.  Lower = better."""
        dx = max(0, b_rect.left() - a_rect.right())
        if b_rect.top() > a_rect.bottom():
            dy = b_rect.top() - a_rect.bottom()
        elif b_rect.bottom() < a_rect.top():
            dy = a_rect.top() - b_rect.bottom()
        else:
            dy = 0
        overlap_bonus = 1000 if dy == 0 else 0
        return dx + dy * 3 - overlap_bonus

    @staticmethod
    def _distance_down(a_rect, b_rect):
        """Score from a to b when b is below a.  Lower = better."""
        dy = max(0, b_rect.top() - a_rect.bottom())
        if b_rect.left() > a_rect.right():
            dx = b_rect.left() - a_rect.right()
        elif b_rect.right() < a_rect.left():
            dx = a_rect.left() - b_rect.right()
        else:
            dx = 0
        overlap_bonus = 1000 if dx == 0 else 0
        return dy + dx * 3 - overlap_bonus

    # ───────────────────── Fallback wrapping ─────────────────────────────

    def _row_wrap_fallback(self, current, direction):
        """Wrap left/right if we're in a row with multiple items."""
        rows = {}
        for t in self._navigation_targets():
            y = self._widget_rect_global(t).center().y()
            band = round(y / 40) * 40
            rows.setdefault(band, []).append(t)

        for band in sorted(rows):
            row = rows[band]
            if current not in row:
                continue
            idx = row.index(current)
            delta = -1 if direction == "left" else 1
            new_idx = (idx + delta) % len(row)
            return row[new_idx]
        return None

    def _column_wrap_fallback(self, current, direction):
        """Wrap up/down if we're in a column with multiple items."""
        cols = {}
        for t in self._navigation_targets():
            x = self._widget_rect_global(t).center().x()
            band = round(x / 80) * 80
            cols.setdefault(band, []).append(t)

        for band in sorted(cols):
            col = cols[band]
            if current not in col:
                continue
            idx = col.index(current)
            delta = -1 if direction == "up" else 1
            new_idx = (idx + delta) % len(col)
            return col[new_idx]
        return None

    # ───────────────────── Activation (Select/Enter) ─────────────────────

    def activate_remote_target(self):
        self.activate_highlighted()

    def activate_highlighted(self):
        target = self._remote_highlighted
        if target is None:
            target = self.queue_panel.list_widget
            if target.count():
                self._set_highlight(target)
            return

        print(f"[NAV] select: {self._target_name(target)}", flush=True)

        if isinstance(target, QListWidget):
            item = target.currentItem()
            if item:
                item.setSelected(True)
                target.itemActivated.emit(item)
        elif isinstance(target, QAbstractButton):
            target.animateClick()
        elif isinstance(target, QLineEdit):
            target.setFocus(Qt.OtherFocusReason)
            target.selectAll()
        elif isinstance(target, QSlider):
            target.setFocus(Qt.OtherFocusReason)
        elif isinstance(target, QComboBox):
            target.showPopup()
        elif target is self.cover_widget:
            self.fullscreen_requested.emit()

        self._remote_clear_timer.start()

    # ───────────────────── Highlight helpers ─────────────────────────────

    def _set_highlight(self, target):
        if self._remote_highlighted is not None:
            self._remote_highlighted.setProperty("remoteHighlight", False)
            if not self._is_fullscreen_nav():
                self._refresh_widget_style(self._remote_highlighted)

        self._remote_highlighted = target
        target.setFocus(Qt.OtherFocusReason)

        if not self._is_fullscreen_nav():
            target.setProperty("remoteHighlight", True)
            self._refresh_widget_style(target)

        if target is self.queue_panel.list_widget and target.count():
            current_row = target.currentRow()
            if current_row < 0:
                found = -1
                for i in range(target.count()):
                    item = target.item(i)
                    if item and item.font().bold():
                        found = i
                        break
                target.setCurrentRow(found if found >= 0 else 0)
            target.scrollToItem(target.currentItem())

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
            name = target.objectName() or ""
            return "search" if "search" in name.lower() else "filter"
        if isinstance(target, QComboBox):
            return f"combo[{target.currentText() or target.objectName()}]"
        if isinstance(target, QSlider):
            name = target.objectName() or ""
            if "volume" in name.lower():
                return "slider[volume]"
            return "slider[seek]"
        if isinstance(target, CoverWidget):
            return "cover_art"
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

    # ───────────────────── Standard methods ──────────────────────────────

    def on_queue_item_selected(self, item):
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
        self._selected_queue_item = None
        self.queue_panel.add_items(results, preserve_order=preserve_order, current_item_source=current_item_source)

    def display_library(self, songs, preserve_order=False, current_item_source=None):
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
        self._selected_queue_item = None
        self.queue_panel.add_items(playlists, preserve_order=preserve_order, current_item_source=current_item_source)

    def get_current_queue_urls(self):
        return self.queue_panel.get_all_urls()

    def get_current_track_index(self):
        return self.queue_panel.get_current_index()

    def update_player_time(self, current, total):
        self.player_bar.set_time_label(current, total)

    def set_volume(self, value):
        self.player_bar.set_volume(value)
