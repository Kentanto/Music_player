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
from PySide6.QtCore import QEvent, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap, QKeyEvent

from .eq_visualizer import EQVisualizer


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cover_label = QLabel("No artwork")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.cover_label.setObjectName("fullscreenArtwork")
        layout.addWidget(self.cover_label, 1)

        info_panel = QWidget()
        info_panel.setObjectName("fullscreenInfoPanel")
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(28, 20, 28, 22)
        info_layout.setSpacing(8)

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

        seek_row = QHBoxLayout()
        self.current_time_label = QLabel("0:00")
        self.total_time_label = QLabel("0:00")
        self.current_time_label.setObjectName("fullscreenTime")
        self.total_time_label.setObjectName("fullscreenTime")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 100)
        self.seek_slider.setObjectName("fullscreenSeek")
        self.seek_slider.sliderMoved.connect(
            lambda value: self.seek_requested.emit(value / 100.0)
        )
        seek_row.addWidget(self.current_time_label)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.total_time_label)
        info_layout.addLayout(seek_row)

        normal_controls = QWidget()
        controls = QGridLayout(normal_controls)
        controls.setContentsMargins(0, 2, 0, 0)

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
        self.eq_visualizer.setFixedHeight(82)
        self.eq_visualizer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        controls_container = QWidget()
        controls_stack = QStackedLayout(controls_container)
        controls_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        controls_stack.addWidget(normal_controls)
        controls_stack.addWidget(self.eq_visualizer)
        info_layout.addWidget(controls_container)
        self._controls_container = controls_container
        self._controls_stack = controls_stack
        layout.addWidget(info_panel, 0)

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
        self.current_time_label.setText(self._format_time(current))
        self.total_time_label.setText(self._format_time(total))

    def set_seek_position(self, position):
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(int(max(0.0, min(position, 1.0)) * 100))
        self.seek_slider.blockSignals(False)

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
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_cover_art()

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
        target = self.eq_visualizer if idle else self._controls_stack.widget(0)
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
            width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.cover_label.setText("")
        self.cover_label.setPixmap(scaled)
        self._last_cover_size = target_size
