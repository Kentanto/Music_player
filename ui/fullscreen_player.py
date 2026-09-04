from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QKeyEvent


class FullscreenPlayer(QWidget):
    """Large now-playing view controlled by the existing player signals."""

    play_pause_clicked = Signal()
    next_clicked = Signal()
    prev_clicked = Signal()
    volume_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover_pixmap = QPixmap()
        self._is_playing = False
        self.setWindowTitle("Music Engine")
        self.setWindowFlag(Qt.Window)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)

        top_row = QHBoxLayout()
        top_row.addStretch()
        self.close_button = QPushButton("Exit Fullscreen")
        self.close_button.clicked.connect(self.close)
        top_row.addWidget(self.close_button)
        layout.addLayout(top_row)

        self.cover_label = QLabel("No artwork")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(300, 300)
        self.cover_label.setStyleSheet(
            "background-color: #121212; border: 1px solid #404040;"
        )
        layout.addWidget(self.cover_label, 1)

        self.title_label = QLabel("No track selected")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setStyleSheet("font-size: 16px; color: #b3b3b3;")
        layout.addWidget(self.artist_label)

        controls = QHBoxLayout()
        controls.setAlignment(Qt.AlignCenter)
        self.prev_button = QPushButton("Previous")
        self.prev_button.clicked.connect(self.prev_clicked.emit)
        controls.addWidget(self.prev_button)

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self.play_pause_clicked.emit)
        controls.addWidget(self.play_pause_button)

        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.next_clicked.emit)
        controls.addWidget(self.next_button)
        layout.addLayout(controls)

        volume_row = QHBoxLayout()
        volume_row.setContentsMargins(80, 0, 80, 0)
        volume_row.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(30)
        self.volume_slider.valueChanged.connect(self.volume_changed.emit)
        volume_row.addWidget(self.volume_slider)
        layout.addLayout(volume_row)

    def set_track_info(self, title, artist="Unknown Artist"):
        self.title_label.setText(title or "No track selected")
        self.artist_label.setText(artist or "Unknown Artist")

    def set_cover_art(self, pixmap):
        self._cover_pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self._fit_cover_art()

    def set_play_pause_state(self, playing):
        self._is_playing = bool(playing)
        self.play_pause_button.setText("Pause" if self._is_playing else "Play")

    def set_volume(self, value):
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)

    def navigation_targets(self):
        return [
            self.close_button,
            self.prev_button,
            self.play_pause_button,
            self.next_button,
            self.volume_slider,
        ]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_cover_art()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def _fit_cover_art(self):
        if self._cover_pixmap.isNull():
            self.cover_label.clear()
            self.cover_label.setText("No artwork")
            return

        width = self.cover_label.width()
        height = self.cover_label.height()
        if width <= 0 or height <= 0:
            return

        scaled = self._cover_pixmap.scaled(
            width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.cover_label.setText("")
        self.cover_label.setPixmap(scaled)