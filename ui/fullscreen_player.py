from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QGraphicsOpacityEffect,
    QApplication,
)
from PySide6.QtCore import QEvent, QPoint, QPropertyAnimation, QTimer, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPixmap, QRegion, QIcon

from keep_awake import ScreenWakeLock

from .clickable_slider import FocusAwareSlider
from .eq_visualizer import EQVisualizer
from .drawn_icons import next_icon, pause_icon, play_icon, prev_icon, shuffle_icon


class EQProgressBadge(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1ed760"))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class FullscreenInfoPanel(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, Qt.transparent)
        gradient.setColorAt(0.28, QColor(30, 30, 30, 155))
        gradient.setColorAt(0.58, QColor(30, 30, 30, 235))
        gradient.setColorAt(1.0, QColor(30, 30, 30, 255))
        painter.fillRect(self.rect(), gradient)
        super().paintEvent(event)


class FullscreenPlayer(QWidget):
    """Large edge-to-edge now-playing view controlled by the existing player."""

    IDLE_TIMEOUT_MS = 5000

    play_pause_clicked = Signal()
    next_clicked = Signal()
    prev_clicked = Signal()
    shuffle_toggled = Signal(bool)
    volume_changed = Signal(int)
    seek_requested = Signal(float)


    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover_pixmap = QPixmap()
        self._last_cover_size = None
        self._is_playing = False
        self._idle_mode = False
        self._cursor_hidden = False
        self._last_wake_from_press = False  # press that left idle mode (touch)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(self.IDLE_TIMEOUT_MS)
        self._idle_timer.timeout.connect(lambda: self._set_idle_mode(True))
        self._transition_animation = None

        # Prevent screen blanking / suspend while fullscreen is active.
        # The lock asks the desktop directly (D-Bus screensaver inhibit on
        # Linux, SetThreadExecutionState on Windows) — faked keypresses
        # never worked on Wayland.
        self._wake_lock = ScreenWakeLock()

        self.setWindowTitle("Music Engine")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self._init_ui()

    def _init_ui(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cover_label = QLabel("No artwork")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.cover_label.setObjectName("fullscreenArtwork")
        layout.addWidget(self.cover_label, 0, 0)

        info_panel = FullscreenInfoPanel()
        info_panel.setObjectName("fullscreenInfoPanel")
        info_panel.setMinimumHeight(300)
        info_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # The whole bottom panel is a click/touch "safe area": presses inside
        # it never exit fullscreen (see in_click_safe_area), so users can aim
        # at the transport buttons without risking an accidental close.
        self._info_panel = info_panel
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(28, 42, 28, 22)
        info_layout.setSpacing(10)

        metadata_panel = QWidget()
        metadata_layout = QVBoxLayout(metadata_panel)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(8)

        self.title_label = QLabel("No track selected")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setObjectName("fullscreenTitle")
        metadata_layout.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setObjectName("fullscreenArtist")
        metadata_layout.addWidget(self.artist_label)

        info_layout.addWidget(metadata_panel)

        self.seek_slider = FocusAwareSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 100)
        self.seek_slider.setObjectName("fullscreenSeek")
        self.seek_slider.sliderMoved.connect(
            lambda value: self.seek_requested.emit(value / 100.0)
        )
        self.seek_slider.focus_changed.connect(self._on_seek_focus_changed)

        normal_controls = QWidget()
        controls = QGridLayout(normal_controls)
        controls.setContentsMargins(0, 2, 0, 0)

        normal_timestamp_row = QWidget()
        normal_timestamp_row.setObjectName("fullscreenNormalTimestampRow")
        timestamp_layout = QHBoxLayout(normal_timestamp_row)
        timestamp_layout.setContentsMargins(12, 0, 12, 0)
        timestamp_layout.setSpacing(8)
        self.normal_current_time_label = QLabel("0:00")
        self.normal_total_time_label = QLabel("0:00")
        self.normal_current_time_label.setObjectName("fullscreenNormalTime")
        self.normal_total_time_label.setObjectName("fullscreenNormalTime")
        timestamp_layout.addWidget(self.normal_current_time_label, 0, Qt.AlignVCenter)
        timestamp_layout.addWidget(self.seek_slider, 1, Qt.AlignVCenter)
        timestamp_layout.addWidget(self.normal_total_time_label, 0, Qt.AlignVCenter)
        controls.addWidget(normal_timestamp_row, 1, 0)

        transport_controls = QHBoxLayout()
        transport_controls.setSpacing(8)
        self.volume_label_widget = QLabel("Volume")
        self.volume_label_widget.setObjectName("fullscreenVolumeLabel")
        volume_control = QWidget()
        volume_control.setObjectName("fullscreenVolumeControl")
        volume_layout = QHBoxLayout(volume_control)
        volume_layout.setContentsMargins(10, 4, 10, 4)
        volume_layout.setSpacing(7)
        volume_layout.addWidget(self.volume_label_widget)

        self.volume_slider = FocusAwareSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(30)
        self.volume_slider.setObjectName("fullscreenVolume")
        self.volume_slider.valueChanged.connect(self.volume_changed.emit)
        self.volume_slider.focus_changed.connect(self._on_volume_focus_changed)
        volume_layout.addWidget(self.volume_slider)
        controls.addWidget(volume_control, 0, 0, Qt.AlignRight | Qt.AlignVCenter)

        # Invisible spacer mirroring the shuffle button so the transport row
        # is symmetric and the play/pause button sits exactly at the screen's
        # horizontal center instead of between pause and next.  Width = the
        # 58px control width from styles.qss + its 1px borders on each side.
        left_spacer = QWidget()
        left_spacer.setFixedSize(60, 42)
        transport_controls.addWidget(left_spacer)

        self.prev_button = QPushButton()
        self.prev_button.setObjectName("fullscreenControl")
        self.prev_button.setToolTip("Previous track")
        self.prev_button.setIconSize(QSize(24, 24))
        self.prev_button.setIcon(QIcon(prev_icon(24, "#f2f2f2")))
        self.prev_button.clicked.connect(self.prev_clicked.emit)
        transport_controls.addWidget(self.prev_button)

        self.play_pause_button = QPushButton()
        self.play_pause_button.setObjectName("fullscreenPlayControl")
        self.play_pause_button.setToolTip("Play or pause")
        self.play_pause_button.setIconSize(QSize(24, 24))
        self.set_play_pause_state(False)
        self.play_pause_button.clicked.connect(self.play_pause_clicked.emit)
        transport_controls.addWidget(self.play_pause_button)

        self.next_button = QPushButton()
        self.next_button.setObjectName("fullscreenControl")
        self.next_button.setToolTip("Next track")
        self.next_button.setIconSize(QSize(24, 24))
        self.next_button.setIcon(QIcon(next_icon(24, "#f2f2f2")))
        self.next_button.clicked.connect(self.next_clicked.emit)
        transport_controls.addWidget(self.next_button)

        self.shuffle_button = QPushButton()
        self.shuffle_button.setObjectName("fullscreenControl")
        self.shuffle_button.setToolTip("Shuffle")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setIconSize(QSize(24, 24))
        self._shuffle_enabled = False
        self._update_shuffle_icon()
        self.shuffle_button.toggled.connect(self._on_shuffle_toggled)
        transport_controls.addWidget(self.shuffle_button)

        controls.addLayout(transport_controls, 0, 0, Qt.AlignCenter)
        self.eq_visualizer = EQVisualizer()
        self.eq_visualizer.setMinimumWidth(0)
        self.eq_visualizer.setFixedHeight(165)
        self.eq_visualizer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.eq_visualizer.set_show_progress(True)
        self.eq_visualizer.progress_clicked.connect(self.seek_requested.emit)

        eq_progress_overlay = QWidget()
        eq_progress_overlay.setObjectName("fullscreenEQOverlay")
        eq_progress_overlay.setMinimumHeight(165)
        eq_progress_layout = QGridLayout(eq_progress_overlay)
        eq_progress_layout.setContentsMargins(0, 0, 0, 0)
        eq_progress_layout.setSpacing(0)
        eq_progress_layout.addWidget(self.eq_visualizer, 0, 0)

        self.eq_seek_slider = FocusAwareSlider(Qt.Horizontal)
        self.eq_seek_slider.setRange(0, 100)
        self.eq_seek_slider.setObjectName("fullscreenEQSeek")
        self.eq_seek_slider.sliderMoved.connect(
            lambda value: self.seek_requested.emit(value / 100.0)
        )
        self.eq_seek_slider.setParent(eq_progress_overlay)
        self.eq_seek_slider.setFixedHeight(15)
        self.eq_seek_slider.show()

        self.eq_progress_badge = EQProgressBadge(eq_progress_overlay)
        self.eq_progress_badge.setObjectName("fullscreenEQProgressBadge")
        self.eq_progress_badge.setFixedSize(18, 18)
        self.eq_progress_badge.setMask(QRegion(self.eq_progress_badge.rect(), QRegion.Ellipse))
        # Clicks on the badge must reach the seek slider underneath it.
        self.eq_progress_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.eq_progress_badge.raise_()
        self._seek_position = 0.0
        self._eq_progress_overlay = eq_progress_overlay

        controls_container = QWidget()
        controls_stack = QStackedLayout(controls_container)
        controls_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        controls_stack.addWidget(normal_controls)
        controls_stack.addWidget(eq_progress_overlay)
        info_layout.addWidget(controls_container)
        self._controls_container = controls_container
        self._controls_stack = controls_stack
        layout.addWidget(info_panel, 0, 0, Qt.AlignBottom)

    def set_track_info(self, title, artist="Unknown Artist"):
        self.title_label.setText(title or "No track selected")
        self.artist_label.setText(artist or "Unknown Artist")

    def set_cover_art(self, pixmap):
        self._cover_pixmap = QPixmap(pixmap) if pixmap and not pixmap.isNull() else QPixmap()
        self._last_cover_size = None
        self._fit_cover_art()

    def set_play_pause_state(self, playing):
        self._is_playing = bool(playing)
        icon = pause_icon(24, "#101010") if self._is_playing else play_icon(24, "#101010")
        self.play_pause_button.setIcon(QIcon(icon))

    def set_shuffle_state(self, enabled):
        """Mirror the player's shuffle state without re-emitting toggled."""
        self._shuffle_enabled = bool(enabled)
        self.shuffle_button.blockSignals(True)
        self.shuffle_button.setChecked(self._shuffle_enabled)
        self.shuffle_button.blockSignals(False)
        self._update_shuffle_icon()

    def _on_shuffle_toggled(self, checked):
        self._shuffle_enabled = bool(checked)
        self._update_shuffle_icon()
        self.shuffle_toggled.emit(self._shuffle_enabled)

    def _update_shuffle_icon(self):
        # Checked state paints the button green, so the symbol goes dark.
        color = "#101010" if self._shuffle_enabled else "#f2f2f2"
        self.shuffle_button.setIcon(QIcon(shuffle_icon(24, color)))

    def in_click_safe_area(self, widget):
        """True inside the bottom controls area — the touch/click safe zone.

        Presses here must never exit fullscreen, so touch users can aim at
        the transport buttons freely without risking an accidental close.
        """
        panel = getattr(self, "_info_panel", None)
        if panel is None or widget is None:
            return False
        return widget is panel or (
            isinstance(widget, QWidget) and panel.isAncestorOf(widget)
        )

    def set_volume(self, value):
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)

    def set_time(self, current, total):
        current_text = self._format_time(current)
        total_text = self._format_time(total)
        self.normal_current_time_label.setText(current_text)
        self.normal_total_time_label.setText(total_text)

    def _on_seek_focus_changed(self, focused: bool):
        color = "#1ed760" if focused else "#f2f2f2"
        self.normal_current_time_label.setStyleSheet(f"color: {color};")
        self.normal_total_time_label.setStyleSheet(f"color: {color};")

    def _on_volume_focus_changed(self, focused: bool):
        color = "#1ed760" if focused else "#c4c4c4"
        self.volume_label_widget.setStyleSheet(f"color: {color};")

    def set_seek_position(self, position):
        self._seek_position = max(0.0, min(float(position), 1.0))
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(int(self._seek_position * 100))
        self.seek_slider.blockSignals(False)
        self.eq_seek_slider.blockSignals(True)
        self.eq_seek_slider.setValue(int(self._seek_position * 100))
        self.eq_seek_slider.blockSignals(False)
        self.eq_visualizer.set_progress(self._seek_position)
        self._position_time_badge()

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds or 0))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def navigation_targets(self):
        return [
            self.volume_slider,
            self.prev_button,
            self.play_pause_button,
            self.next_button,
            self.shuffle_button,
            self.seek_slider,
            self.eq_seek_slider,
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_cover_art()
        self._position_eq_seekbar()
        self._position_time_badge()

    def _position_eq_seekbar(self):
        if not hasattr(self, "_eq_progress_overlay"):
            return
        visualizer_position = self.eq_visualizer.mapTo(
            self._eq_progress_overlay, QPoint(0, 0)
        )
        baseline = self.eq_visualizer.height() - 23
        left = visualizer_position.x() + 12
        width = max(1, self.eq_visualizer.width() - 24)
        self.eq_seek_slider.setGeometry(
            left,
            max(0, visualizer_position.y() + baseline - self.eq_seek_slider.height() // 2),
            width,
            self.eq_seek_slider.height(),
        )
        self.eq_seek_slider.raise_()

    def _position_time_badge(self):
        if not hasattr(self, "_eq_progress_overlay"):
            return
        self._position_eq_seekbar()
        badge = self.eq_progress_badge
        progress_left = 12
        progress_right = max(progress_left, self.eq_visualizer.width() - 12)
        center_x = progress_left + (progress_right - progress_left) * self._seek_position
        visualizer_position = self.eq_visualizer.mapTo(self._eq_progress_overlay, QPoint(0, 0))
        badge.move(
            int(visualizer_position.x() + center_x - badge.width() / 2),
            max(
                0,
                visualizer_position.y()
                + self.eq_visualizer.height()
                - 23
                - badge.height() // 2
                - 1,
            ),
        )
        badge.raise_()

    def keyPressEvent(self, event: QKeyEvent):
        self._register_activity()
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)
        self._register_activity()
        self._wake_lock.acquire()

    def hideEvent(self, event):
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self._idle_timer.stop()
        self._wake_lock.release()
        self._set_cursor_hidden(False)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        activity_events = {
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
        }
        is_fullscreen_child = watched is self or (
            isinstance(watched, QWidget) and self.isAncestorOf(watched)
        )
        if is_fullscreen_child and event.type() in activity_events:
            if event.type() == QEvent.Type.MouseButtonPress and self._idle_mode:
                # A press that wakes the idle view up: the touch handler
                # treats exactly this one press as "wake only" (it must not
                # also exit fullscreen) and consumes the flag.
                self._last_wake_from_press = True
            self._register_activity()
        return super().eventFilter(watched, event)

    def _register_activity(self):
        if not self.isVisible():
            return
        self._idle_timer.start()
        self._set_idle_mode(False)

    def _set_idle_mode(self, idle):
        if self._idle_mode == idle:
            return
        self._idle_mode = idle
        self._set_cursor_hidden(idle)
        target = self._eq_progress_overlay if idle else self._controls_stack.widget(0)
        self._controls_stack.setCurrentWidget(target)

        effect = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        self._transition_animation = QPropertyAnimation(effect, b"opacity", self)
        self._transition_animation.setDuration(350)
        self._transition_animation.setStartValue(0.0)
        self._transition_animation.setEndValue(1.0)
        self._transition_animation.start()

    def _set_cursor_hidden(self, hidden):
        if self._cursor_hidden == hidden:
            return

        application = QApplication.instance()
        if application is None:
            return

        if hidden:
            application.setOverrideCursor(Qt.CursorShape.BlankCursor)
        elif application.overrideCursor() is not None:
            application.restoreOverrideCursor()
        self._cursor_hidden = hidden

    def _fit_cover_art(self):
        if self._cover_pixmap.isNull():
            self.cover_label.clear()
            self.cover_label.setText("No artwork")
            return

        width = self.cover_label.width()
        height = self.cover_label.height()
        if width <= 0 or height <= 0:
            return
        target_size = (width, height)
        if self._last_cover_size == target_size:
            return

        scaled = self._cover_pixmap.scaled(
            width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        x_offset = max(0, (scaled.width() - width) // 2)
        y_offset = max(0, (scaled.height() - height) // 2)
        self.cover_label.setText("")
        self.cover_label.setPixmap(scaled.copy(x_offset, y_offset, width, height))
        self._last_cover_size = target_size
