from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QSlider, QLabel, QVBoxLayout
)
from PySide6.QtCore import Qt, Signal


class PlayerBar(QWidget):
    """Player controls: play/pause toggle, next, prev, volume, seek"""
    
    play_pause_clicked = Signal()
    next_clicked = Signal()
    prev_clicked = Signal()
    shuffle_toggled = Signal(bool)
    volume_changed = Signal(int)
    seek_requested = Signal(float)  # 0.0 to 1.0
    fullscreen_requested = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # ===== Seek bar =====
        seek_layout = QHBoxLayout()
        self.time_label_start = QLabel("0:00")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 100)
        self.seek_slider.sliderMoved.connect(self.on_seek)  # Only on manual drag
        self.time_label_end = QLabel("0:00")
        
        seek_layout.addWidget(self.time_label_start)
        seek_layout.addWidget(self.seek_slider)
        seek_layout.addWidget(self.time_label_end)
        main_layout.addLayout(seek_layout)

        self.track_label = QLabel("No track selected")
        self.track_label.setAlignment(Qt.AlignCenter)
        self.track_label.setWordWrap(True)
        main_layout.addWidget(self.track_label)
        
        # ===== Controls =====
        controls_layout = QHBoxLayout()
        
        # Previous button
        self.prev_btn = QPushButton("⏮ Prev")
        self.prev_btn.clicked.connect(self.prev_clicked.emit)
        controls_layout.addWidget(self.prev_btn)
        
        # Play/pause toggle button
        self.play_pause_btn = QPushButton("▶ Play")
        self.play_pause_btn.clicked.connect(self.play_pause_clicked.emit)
        controls_layout.addWidget(self.play_pause_btn)
        
        # Next button
        self.next_btn = QPushButton("Next ⏭")
        self.next_btn.clicked.connect(self.next_clicked.emit)
        controls_layout.addWidget(self.next_btn)

        self.fullscreen_btn = QPushButton("Fullscreen")
        self.fullscreen_btn.clicked.connect(self.fullscreen_requested.emit)
        controls_layout.addWidget(self.fullscreen_btn)

        self.shuffle_btn = QPushButton("🔀 Shuffle")
        self.shuffle_btn.setObjectName("shuffleButton")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.toggled.connect(self.shuffle_toggled.emit)
        controls_layout.addWidget(self.shuffle_btn)
        
        controls_layout.addStretch()
        main_layout.addLayout(controls_layout)
        
        # ===== Volume =====
        volume_layout = QHBoxLayout()
        volume_label = QLabel("🔊 Vol")
        volume_layout.addWidget(volume_label)
        
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(30)
        self.volume_slider.setMaximumWidth(100)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        volume_layout.addWidget(self.volume_slider)
        
        self.volume_label = QLabel("30")
        volume_layout.addWidget(self.volume_label)
        volume_layout.addStretch()
        
        main_layout.addLayout(volume_layout)
        
        self.setLayout(main_layout)
    
    def on_seek(self, value):
        """Emit seek position as 0.0-1.0"""
        position = value / 100.0
        self.seek_requested.emit(position)
    
    def set_seek_position(self, position):
        """Set seek slider position (0.0-1.0)"""
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(int(position * 100))
        self.seek_slider.blockSignals(False)
    
    def set_time_label(self, current, total):
        """Update time display (format: "0:00")"""
        self.time_label_start.setText(self._format_time(current))
        self.time_label_end.setText(self._format_time(total))
    
    @staticmethod
    def _format_time(seconds):
        """Convert seconds to "m:ss" format"""
        if seconds < 0:
            seconds = 0
        mins = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{mins}:{secs:02d}"
    
    def set_volume(self, value):
        """Set volume slider value"""
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_label.setText(str(value))
        self.volume_slider.blockSignals(False)

    def set_track_info(self, title):
        self.track_label.setText(title or "No track selected")

    def set_shuffle_state(self, enabled: bool):
        self.shuffle_btn.blockSignals(True)
        self.shuffle_btn.setChecked(enabled)
        self.shuffle_btn.blockSignals(False)
    
    def set_play_pause_state(self, playing: bool):
        """Update the button label based on playback state."""
        self.play_pause_btn.setText("⏸ Pause" if playing else "▶ Play")

    def on_volume_changed(self, value):
        """Handle volume slider changes"""
        self.volume_label.setText(str(value))
        self.volume_changed.emit(value)
