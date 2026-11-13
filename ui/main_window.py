import time

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QGridLayout,
    QApplication, QAbstractButton, QLineEdit, QListWidget, QSlider, QComboBox,
    QPushButton, QMenu,
)
from PySide6.QtCore import Qt, Signal, QTimer, QEvent, QRect, QPoint
from PySide6.QtGui import QKeyEvent, QShortcut, QKeySequence


from .sidebar import Sidebar
from .search_panel import SearchPanel
from .queue_panel import QueuePanel
from .player_bar import PlayerBar
from .touch_player_bar import TouchPlayerBar
from .wedge_volume import WedgeVolumeSlider
from .cover_widget import CoverWidget
from .fullscreen_player import FullscreenPlayer
from .eq_visualizer import EQVisualizer
from .toast import ToastManager
from .touch_gestures import TouchGestureHandler


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
    link_spotify_requested = Signal()
    view_failures_requested = Signal()
    network_sync_requested = Signal()
    open_playlist_requested = Signal(object)
    stop_requested = Signal()
    volume_mute = Signal()
    volume_changed = Signal(int)
    seek_requested = Signal(float)
    seek_delta_requested = Signal(int)
    fullscreen_requested = Signal()
    track_selected = Signal(object)
    back_requested = Signal()
    stop_requested = Signal()
    select_requested = Signal()
    settings_requested = Signal()
    touch_mode_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Music Engine")
        self.resize(1200, 800)
        self._touch_mode = False
        self.init_ui()
        self.toast_manager = ToastManager(self)
        self._selected_queue_item = None
        self.connect_signals()
        self._install_nav_filters()

        # Touch-screen gestures: hold = right click, drag = list scroll
        # with flick momentum; in fullscreen a tap on empty space (touch
        # or mouse) closes the view, unless it just woke the idle EQ view
        # or landed on the safe bottom controls.
        self._touch_gestures = TouchGestureHandler(self)
        self._touch_gestures.queue_swipe_right.connect(self._on_queue_swipe_right)
        self._touch_gestures.queue_swipe_left.connect(self._on_queue_swipe_left)
        QApplication.instance().installEventFilter(self._touch_gestures)

    def _on_queue_swipe_right(self, item_data):
        self.queue_next_requested.emit(item_data)

    def _on_queue_swipe_left(self, item_data):
        self.queue_panel.soft_remove_item(item_data)

    def init_ui(self):
        central = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # ── Top bar: search + hamburger, lives above the queue in the right panel.
        # This lets the left panel (cover art) reach the very top of the window.
        self.top_bar = QWidget()
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # Spacer is hidden; the search bar fills the full top row of the right panel.
        self._search_spacer = QWidget()
        self._search_spacer.setFixedWidth(0)
        self._search_spacer.hide()
        top_layout.addWidget(self._search_spacer)

        self.search_panel = SearchPanel()
        top_layout.addWidget(self.search_panel, 1)

        self.hamburger_btn = QPushButton("☰")
        self.hamburger_btn.setObjectName("hamburgerBtn")
        self.hamburger_btn.setFixedSize(42, 42)
        self.hamburger_btn.clicked.connect(self._show_hamburger_menu)
        top_layout.addWidget(self.hamburger_btn)

        # ── Center: left panel (cover + touch controls) + queue ──
        center_splitter = QSplitter(Qt.Horizontal)

        self.left_panel = QWidget()
        self.left_panel.setMinimumWidth(420)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self.cover_widget = CoverWidget()
        left_layout.addWidget(self.cover_widget, 1)

        self.touch_player_bar = TouchPlayerBar()
        self.touch_player_bar.hide()
        left_layout.addWidget(self.touch_player_bar, 0)

        center_splitter.addWidget(self.left_panel)

        # Right pane: top bar above the queue
        self.right_panel = QWidget()
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(5)
        right_layout.addWidget(self.top_bar, 0)
        self.queue_panel = QueuePanel()
        right_layout.addWidget(self.queue_panel, 1)
        center_splitter.addWidget(self.right_panel)

        center_splitter.setSizes([480, 520])
        main_layout.addWidget(center_splitter, 1)

        # ── Normal-mode player bar: full width at the bottom, outside the
        # thumbnail column so it can never force the column wide. Touch
        # mode swaps it for the touch bar and hides this one. ──
        self.player_bar = PlayerBar()
        self.eq_visualizer = EQVisualizer(self.player_bar)
        self.player_bar.controls_layout.addWidget(self.eq_visualizer, 1)
        self._eq_stretch_added = True
        self.player_bar.controls_layout.addStretch()
        main_layout.addWidget(self.player_bar, 0)

        # Sidebar lives for signals but is never shown
        self.sidebar = Sidebar()
        self.sidebar.hide()

        self.fullscreen_player = FullscreenPlayer(self)

        self._remote_highlighted = None
        self._remote_keep_focus = False
        self._remote_clear_timer = QTimer(self)
        self._remote_clear_timer.setSingleShot(True)
        self._remote_clear_timer.setInterval(8000)
        self._remote_clear_timer.timeout.connect(self.clear_remote_highlight)

        self._navigate_dedupe_window = 0.080
        self._last_navigate_dir = None
        self._last_navigate_time = 0.0
        self._activate_dedupe_window = 0.080
        self._last_activate_time = 0.0

        central.setLayout(main_layout)
        self.setCentralWidget(central)
        # Prevent the window from shrinking so much that touch controls disappear
        self.setMinimumSize(960, 620)

    def _show_hamburger_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { border: 2px solid #a0a0a0; background-color: #282828; padding: 4px; margin: 2px; }"
            "QMenu::item { padding: 10px 24px; font-size: 13px; color: #ffffff; }"
            "QMenu::item:selected { background-color: #1db954; }"
            "QMenu::separator { background-color: #505050; height: 1px; margin: 4px 8px; }"
        )
        menu.addAction("📋 Playlists", self.sidebar.playlists_clicked.emit)
        menu.addAction("➕ Add to Playlist", self.sidebar.add_to_playlist_clicked.emit)
        menu.addAction("Import List", self.sidebar.import_list_clicked.emit)
        menu.addAction("Link Spotify", self.sidebar.link_spotify_clicked.emit)
        menu.addAction("Failed Songs", self.sidebar.view_failures_clicked.emit)
        menu.addAction("🔗 Network Sync", self.sidebar.network_sync_clicked.emit)
        menu.addAction("⚙️ Settings", self.settings_requested.emit)
        menu.addSeparator()
        touch_action = menu.addAction("Touch Panel Mode")
        touch_action.setCheckable(True)
        touch_action.setChecked(self._touch_mode)
        touch_action.triggered.connect(self._toggle_touch_mode)
        # Align menu's right edge with the button's right edge so it doesn't
        # clip past the window edge, and add a small gap below the button.
        btn_rect = self.hamburger_btn.rect()
        global_br = self.hamburger_btn.mapToGlobal(
            QPoint(btn_rect.width(), btn_rect.height())
        )
        menu_width = menu.sizeHint().width()
        pos = QPoint(global_br.x() - menu_width, global_br.y() + 2)
        menu.popup(pos)

    def _toggle_touch_mode(self, enabled):
        self._touch_mode = bool(enabled)
        self.apply_touch_mode(self._touch_mode)
        self.touch_mode_changed.emit(self._touch_mode)

    def apply_touch_mode(self, enabled):
        self._touch_mode = bool(enabled)
        self.queue_panel.set_touch_mode(enabled)
        self.search_panel.set_touch_mode(enabled)
        self.cover_widget.set_fill_pane(enabled)
        # The touch control cluster needs a wider pane; 640 guarantees the
        # media circles and the shuffle/fullscreen pair always fit without
        # the layout squeezing buttons into each other.
        self.left_panel.setMinimumWidth(640 if enabled else 420)
        # Match hamburger size to search bar height in touch mode
        self.hamburger_btn.setFixedSize(52 if enabled else 42, 52 if enabled else 42)
        if enabled:
            # Move EQ from normal → touch
            if self.eq_visualizer.parentWidget() is self.player_bar:
                self.player_bar.controls_layout.removeWidget(self.eq_visualizer)
                self.eq_visualizer.setParent(None)
                if self._eq_stretch_added:
                    item = self.player_bar.controls_layout.takeAt(
                        self.player_bar.controls_layout.count() - 1
                    )
                    if item:
                        del item
                    self._eq_stretch_added = False
                self.touch_player_bar.set_eq_widget(self.eq_visualizer)
            self.player_bar.hide()
            self.touch_player_bar.show()
        else:
            # Move EQ from touch → normal
            w = self.touch_player_bar.remove_eq_widget()
            if w is not None:
                self.player_bar.controls_layout.addWidget(w, 1)
                self.player_bar.controls_layout.addStretch()
                self._eq_stretch_added = True
            self.touch_player_bar.hide()
            self.player_bar.show()
        self._sync_seek_row()
        self.update()

    def _on_cover_geometry(self, image_x, image_width):
        """Keep the touch seek bar edge to edge with the artwork."""
        if self._touch_mode:
            self.touch_player_bar.align_seek_row(
                image_x, image_width, self.cover_widget.width()
            )

    def _sync_seek_row(self):
        panel_width = self.cover_widget.width()
        if panel_width <= 0:
            return
        self.touch_player_bar.align_seek_row(
            self.cover_widget.cover_label_x,
            self.cover_widget.cover_label_w,
            panel_width,
        )

    def _on_cover_clicked(self):
        """Tap/click on the cover art thumbnail: auto-scroll the queue view
        to the currently playing song so it is easy to find again."""
        self.queue_panel.scroll_to_current_track()

    def _update_search_spacer(self):
        pass

    def connect_signals(self):
        self.search_panel.search_requested.connect(self.search_requested.emit)
        # Keep the touch seek bar edge to edge with the artwork
        self.cover_widget.cover_geometry_changed.connect(self._on_cover_geometry)
        # Tap/click on the cover art jumps the queue view to the playing song
        self.cover_widget.cover_clicked.connect(self._on_cover_clicked)
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
        # Touch player bar mirrors the same signals
        self.touch_player_bar.play_pause_clicked.connect(self.play_pause_track.emit)
        self.touch_player_bar.next_clicked.connect(self.next_track.emit)
        self.touch_player_bar.prev_clicked.connect(self.prev_track.emit)
        self.touch_player_bar.shuffle_toggled.connect(self.shuffle_toggled.emit)
        self.touch_player_bar.volume_changed.connect(self.volume_changed.emit)
        self.touch_player_bar.seek_requested.connect(self.seek_requested.emit)
        self.touch_player_bar.fullscreen_requested.connect(self.fullscreen_requested.emit)
        self.fullscreen_player.seek_requested.connect(self.seek_requested.emit)
        self.sidebar.add_to_playlist_clicked.connect(self.add_to_playlist.emit)
        self.sidebar.import_list_clicked.connect(self.import_list_requested.emit)
        self.sidebar.link_spotify_clicked.connect(self.link_spotify_requested.emit)
        self.sidebar.view_failures_clicked.connect(self.view_failures_requested.emit)
        self.sidebar.network_sync_clicked.connect(self.network_sync_requested.emit)
        self.sidebar.playlists_clicked.connect(self.load_playlists.emit)

        fullscreen_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        fullscreen_shortcut.setContext(Qt.ApplicationShortcut)
        fullscreen_shortcut.activated.connect(self.fullscreen_requested.emit)

        # Standard media-key shortcuts for keyboards / OS media events
        QShortcut(QKeySequence(Qt.Key_MediaPlay), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaPause), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaTogglePlayPause), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaNext), self).activated.connect(self.next_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaPrevious), self).activated.connect(self.prev_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaStop), self).activated.connect(self.stop_requested.emit)
        QShortcut(QKeySequence(Qt.Key_VolumeMute), self).activated.connect(self.volume_mute.emit)


    # ───────────────────── Layer-2 interaction state ─────────────────────

    @property
    def _slider_adjust_target(self):
        return getattr(self, "__slider_adjust_target", None)

    @_slider_adjust_target.setter
    def _slider_adjust_target(self, value):
        object.__setattr__(self, "__slider_adjust_target", value)

    # ───────────────────── Event filter installation ─────────────────────

    def _install_nav_filters(self):
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            target = self._find_nav_target_for_widget(watched)
            if target and target is not self._remote_highlighted:
                # Always update internal tracking
                if self._remote_highlighted is not None and self._should_highlight(self._remote_highlighted):
                    self._remote_highlighted.setProperty("remoteHighlight", False)
                    self._refresh_widget_style(self._remote_highlighted)
                self._remote_highlighted = target
                # A text field focused directly by the user (mouse click, tab,
                # window switch) must never lose focus to the idle-clear.
                self._remote_keep_focus = isinstance(target, QLineEdit) and event.reason() in (
                    Qt.MouseFocusReason, Qt.TabFocusReason, Qt.BacktabFocusReason,
                    Qt.ActiveWindowFocusReason, Qt.ShortcutFocusReason,
                )
                # Only show green border when appropriate
                if self._should_highlight(target):
                    target.setProperty("remoteHighlight", True)
                    self._refresh_widget_style(target)
                    self._remote_clear_timer.start()
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            target = self._find_nav_target_for_widget(watched)
            if target is None:
                return super().eventFilter(watched, event)

            # Typing in the highlighted text field means hands-on use: keep
            # focus there and defer the idle-clear while the user is active.
            if target is self._remote_highlighted and isinstance(target, QLineEdit):
                self._remote_keep_focus = True
                self._remote_clear_timer.start()

            # ── Layer check: slider adjust mode ──
            if self._is_slider_like(target) and self._slider_adjust_target is target:
                if key in (Qt.Key_Left, Qt.Key_Right):
                    self._adjust_slider(target, "left" if key == Qt.Key_Left else "right")
                    return True
                # Any other key exits slider adjust mode
                self._exit_slider_adjust(target)
                if key in (Qt.Key_Up, Qt.Key_Down):
                    pass  # fall through to spatial navigation below
                else:
                    # Enter/Space/Escape: consumed, do nothing else
                    return True

            # ── QComboBox popup open: let Qt handle arrows ──
            if isinstance(target, QComboBox) and target.view().isVisible():
                if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                    target.hidePopup()
                    return True
                return super().eventFilter(watched, event)

            # ── Layer 2 enter: Return / Space / Enter = activate ──
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                # When a text field has focus, let Qt handle these keys normally
                # (Enter submits a search, Space inserts a character, etc.)
                if isinstance(target, QLineEdit):
                    return super().eventFilter(watched, event)
                print(f"[NAV] key event: {key} on {self._target_name(target)} -> activate", flush=True)
                self.activate_highlighted()
                return True

            # ── QLineEdit: cursor-edge awareness ──
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

            # ── Layer 1 spatial navigation ──
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

    def _should_highlight(self, target):
        """Return whether a target should get the green highlight border."""
        # Never highlight the invisible eq-seek slider in idle mode
        if target is getattr(self.fullscreen_player, "eq_seek_slider", None):
            return False
        return True

    @staticmethod
    def _is_slider_like(target):
        """Targets that behave like a slider when activated/adjusted.

        The touch-mode volume wedge is a custom-drawn widget, not a QSlider,
        but it exposes value()/setValue() and must take part in the same
        keyboard/remote "slider adjust" flow as the real sliders.
        """
        return isinstance(target, (QSlider, WedgeVolumeSlider))

    def _adjust_slider(self, slider, direction):
        if (
            isinstance(slider, WedgeVolumeSlider)
            or slider is self.player_bar.volume_slider
            or slider is self.fullscreen_player.volume_slider
        ):
            delta = -5 if direction == "left" else 5
            new_val = max(0, min(100, slider.value() + delta))
            slider.setValue(new_val)
            print(f"[NAV] {direction}: volume {new_val}", flush=True)
        else:
            delta = -5 if direction == "left" else 5
            print(f"[NAV] {direction}: seek {delta:+d}s", flush=True)
            self.seek_delta_requested.emit(delta)

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
                self.fullscreen_player.shuffle_button,
                self.fullscreen_player.volume_slider,
            ]
        base = [
            self.search_panel.search_bar,
            self.cover_widget,
            self.queue_panel.filter_input,
            self.queue_panel.sort_combo,
            self.queue_panel.list_widget,
        ]
        if self._touch_mode:
            base.extend([
                self.touch_player_bar.seek_slider,
                self.touch_player_bar.prev_btn,
                self.touch_player_bar.play_pause_btn,
                self.touch_player_bar.next_btn,
                self.touch_player_bar.shuffle_btn,
                self.touch_player_bar.fullscreen_btn,
                self.touch_player_bar.wedge_volume,
            ])
        else:
            base.extend([
                self.player_bar.seek_slider,
                self.player_bar.prev_btn,
                self.player_bar.play_pause_btn,
                self.player_bar.next_btn,
                self.player_bar.shuffle_btn,
                self.player_bar.fullscreen_btn,
                self.player_bar.volume_slider,
            ])
        return base

    def _fullscreen_nav_rows(self):
        """Explicit row grid for fullscreen — avoids broken spatial math on overlays."""
        return [
            [self.fullscreen_player.seek_slider],
            [
                self.fullscreen_player.prev_button,
                self.fullscreen_player.play_pause_button,
                self.fullscreen_player.next_button,
                self.fullscreen_player.shuffle_button,
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

    def on_return_pressed(self):
        """Return/Back key pressed — exit any active mode, then clear highlight."""
        # Exit slider adjust mode if active
        if self._slider_adjust_target is not None:
            self._slider_adjust_target.setProperty("remoteHighlight", False)
            self._refresh_widget_style(self._slider_adjust_target)
            self._slider_adjust_target = None
            self._remote_highlighted = None
            print("[NAV] Return: exited slider adjust mode", flush=True)
            return
        # If a combo popup is open, close it
        if isinstance(self._remote_highlighted, QComboBox):
            self._remote_highlighted.hidePopup()
            return
        # Default: clear highlight
        self.clear_remote_highlight()

    # ───────────────────── Shared navigation engine ──────────────────────

    def navigate_remote(self, direction):
        self.navigate(direction)

    def _exit_slider_adjust(self, slider):
        if self._slider_adjust_target is slider:
            self._slider_adjust_target = None
            self._remote_highlighted = None
            slider.setProperty("remoteHighlight", False)
            self._refresh_widget_style(slider)
            print("[NAV] exited slider adjust mode", flush=True)

    def navigate(self, direction):
        # Dedupe: drop duplicate calls for the same direction within the
        # dedupe window.  This prevents double-firing when both the CEC
        # layer and the Linux keyboard evdev layer emit the same key.
        now = time.monotonic()
        if direction == self._last_navigate_dir and (now - self._last_navigate_time) < self._navigate_dedupe_window:
            print(f"[NAV] {direction}: deduped", flush=True)
            return
        self._last_navigate_dir = direction
        self._last_navigate_time = now

        targets = self._navigation_targets()
        if not targets:
            return

        # Resolve the effective "current" widget
        current = self._slider_adjust_target or self._remote_highlighted
        if current not in targets:
            focused = QApplication.focusWidget()
            if focused:
                for t in targets:
                    if focused is t or t.isAncestorOf(focused):
                        current = t
                        break

        if current not in targets:
            # Auto-start: queue, then sidebar, then first target
            for default in (self.queue_panel.list_widget,
                           self.sidebar.playlists_btn,
                           targets[0] if targets else None):
                if default is not None and default in targets:
                    print(f"[NAV] {direction}: auto-start -> {self._target_name(default)}", flush=True)
                    self._set_highlight(default, from_navigate=True)
                    return
            return

        # ── Queue: up/down scrolls items, at edges leaves ──
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

        # ── Fullscreen: use explicit row grid ──
        if self._is_fullscreen_nav():
            target = self._fullscreen_grid_navigate(current, direction)
            if target:
                print(
                    f"[NAV] {direction}: {self._target_name(current)} -> {self._target_name(target)}",
                    flush=True,
                )
                self._set_highlight(target, from_navigate=True)
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
            self._set_highlight(target, from_navigate=True)

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
        # Dedupe: drop duplicate activate calls within the dedupe window.
        now = time.monotonic()
        if (now - self._last_activate_time) < self._activate_dedupe_window:
            print("[NAV] select: deduped", flush=True)
            return
        self._last_activate_time = now

        target = self._remote_highlighted
        if target is None:
            target = self.queue_panel.list_widget
            if target.count():
                self._set_highlight(target)
                item = self.queue_panel.get_current_item()
                if item:
                    if item.get("type") == "playlist":
                        self.open_playlist_requested.emit(item.get("playlist_id"))
                    else:
                        self.play_track_index.emit(item)
            return

        print(f"[NAV] select: {self._target_name(target)}", flush=True)

        if isinstance(target, QListWidget):
            item = self.queue_panel.get_current_item()
            if item:
                if item.get("type") == "playlist":
                    self.open_playlist_requested.emit(item.get("playlist_id"))
                else:
                    self.play_track_index.emit(item)

        elif isinstance(target, QAbstractButton):
            target.animateClick()

        elif isinstance(target, QLineEdit):
            if target is self.search_panel.search_bar:
                self.search_panel.on_search()
            elif target is self.queue_panel.filter_input:
                item = self.queue_panel.get_current_item()
                if item is None and self.queue_panel.list_widget.count():
                    # Find the currently-playing item, else select row 0
                    found = -1
                    for i in range(self.queue_panel.list_widget.count()):
                        widget_item = self.queue_panel.list_widget.item(i)
                        if widget_item and widget_item.font().bold():
                            found = i
                            break
                    row = found if found >= 0 else 0
                    self.queue_panel.list_widget.setCurrentRow(row)
                    item = self.queue_panel.get_current_item()
                if item:
                    if item.get("type") == "playlist":
                        self.open_playlist_requested.emit(item.get("playlist_id"))
                    else:
                        self.play_track_index.emit(item)
            else:
                target.setFocus(Qt.OtherFocusReason)
                target.selectAll()

        elif self._is_slider_like(target):
            # Layer 2: enter slider adjust mode (also covers the touch-mode
            # volume wedge, which is not a QSlider)
            self._slider_adjust_target = target
            if self._should_highlight(target):
                target.setProperty("remoteHighlight", True)
                self._refresh_widget_style(target)
            print("[NAV] entered slider adjust mode", flush=True)

        elif isinstance(target, QComboBox):
            target.showPopup()

        elif target is self.cover_widget:
            self.fullscreen_requested.emit()

        self._remote_clear_timer.start()

    # ───────────────────── Highlight helpers ─────────────────────────────

    def _set_highlight(self, target, *, from_navigate=False):
        # If coming from spatial navigation and we're in slider adjust mode, exit it
        if from_navigate and self._slider_adjust_target is not None:
            self._exit_slider_adjust(self._slider_adjust_target)
        
        if self._remote_highlighted is not None and self._should_highlight(self._remote_highlighted):
            self._remote_highlighted.setProperty("remoteHighlight", False)
            self._refresh_widget_style(self._remote_highlighted)

        self._remote_highlighted = target
        self._remote_keep_focus = False
        target.setFocus(Qt.OtherFocusReason)

        if self._should_highlight(target):
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
            target.viewport().update()

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
        if hasattr(target, "valueChanged") and "wedge" in target.objectName().lower():
            return "wedge[volume]"
        if isinstance(target, CoverWidget):
            return "cover_art"
        return target.objectName() or target.__class__.__name__

    def clear_remote_highlight(self):
        if self._remote_highlighted is None:
            return
        target = self._remote_highlighted
        target.setProperty("remoteHighlight", False)
        self._refresh_widget_style(target)
        # Only give up focus when the highlight came from remote navigation.
        # A text field the user clicked into or typed in keeps keyboard focus;
        # only the highlight border is removed.
        if not self._remote_keep_focus:
            target.clearFocus()
        self._remote_highlighted = None
        self._remote_keep_focus = False

    @staticmethod
    def _refresh_widget_style(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    # ───────────────────── Standard methods ──────────────────────────────

    def show_notification(self, title, message="", kind="info", duration_ms=5000):
        """Show a non-blocking notification card at the top of the window.

        kind: "success", "error" or "info" (controls the accent color).
        Cards stack, auto-dismiss after duration_ms and close on click.
        """
        self.toast_manager.show_notification(title, message, kind, duration_ms)

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
        self.touch_player_bar.set_time_label(current, total)

    def set_volume(self, value):
        self.player_bar.set_volume(value)
        self.touch_player_bar.set_volume(value)

    def set_seek_position(self, position):
        self.player_bar.set_seek_position(position)
        self.touch_player_bar.set_seek_position(position)

    def set_play_pause_state(self, playing):
        self.player_bar.set_play_pause_state(playing)
        self.touch_player_bar.set_play_pause_state(playing)

    def set_shuffle_state(self, enabled):
        self.player_bar.set_shuffle_state(enabled)
        self.touch_player_bar.set_shuffle_state(enabled)

    def set_track_info(self, title):
        self.player_bar.set_track_info(title)
