from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSizePolicy,
    QStackedLayout,
    QGraphicsOpacityEffect,
    QApplication,
)
from PySide6.QtCore import QEvent, QPoint, QPropertyAnimation, QTimer, QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPixmap, QRegion

from .eq_visualizer import EQVisualizer


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
    volume_changed = Signal(int)
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover_pixmap = QPixmap()
        self._last_cover_size = None
        self._is_playing = False
        self._idle_mode = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(self.IDLE_TIMEOUT_MS)
        self._idle_timer.timeout.connect(lambda: self._set_idle_mode(True))
        self._transition_animation = None
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

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 100)
        self.seek_slider.setObjectName("fullscreenSeek")
        self.seek_slider.sliderMoved.connect(
            lambda value: self.seek_requested.emit(value / 100.0)
        )

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
        volume_label = QLabel("Volume")
        volume_label.setObjectName("fullscreenVolumeLabel")
        volume_control = QWidget()
        volume_control.setObjectName("fullscreenVolumeControl")
        volume_layout = QHBoxLayout(volume_control)
        volume_layout.setContentsMargins(10, 4, 10, 4)
        volume_layout.setSpacing(7)
        volume_layout.addWidget(volume_label)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(30)
        self.volume_slider.setObjectName("fullscreenVolume")
        self.volume_slider.valueChanged.connect(self.volume_changed.emit)
        volume_layout.addWidget(self.volume_slider)
        controls.addWidget(volume_control, 0, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.prev_button = QPushButton("<<")
        self.prev_button.setObjectName("fullscreenControl")
        self.prev_button.setToolTip("Previous track")
        self.prev_button.clicked.connect(self.prev_clicked.emit)
        transport_controls.addWidget(self.prev_button)

        self.play_pause_button = QPushButton(">")
        self.play_pause_button.setObjectName("fullscreenPlayControl")
        self.play_pause_button.setToolTip("Play or pause")
        self.play_pause_button.clicked.connect(self.play_pause_clicked.emit)
        transport_controls.addWidget(self.play_pause_button)

        self.next_button = QPushButton(">>")
        self.next_button.setObjectName("fullscreenControl")
        self.next_button.setToolTip("Next track")
        self.next_button.clicked.connect(self.next_clicked.emit)
        transport_controls.addWidget(self.next_button)
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

        self.eq_seek_slider = QSlider(Qt.Horizontal)
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
        self.play_pause_button.setText("||" if self._is_playing else ">")

    def set_volume(self, value):
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)

    def set_time(self, current, total):
        current_text = self._format_time(current)
        total_text = self._format_time(total)
        self.normal_current_time_label.setText(current_text)
        self.normal_total_time_label.setText(total_text)

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
                + 1,
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

    def hideEvent(self, event):
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self._idle_timer.stop()
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
