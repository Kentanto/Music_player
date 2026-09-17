from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap


class CoverWidget(QWidget):
    """Displays current track: cover art, title, artist"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover_pixmap = QPixmap()
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setAlignment(Qt.AlignCenter)
        
        # Let the artwork fill the available pane instead of letterboxing.
        self.cover_label = QLabel("🎵")
        self.cover_label.setFont(QFont("Arial", 80))
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(240, 135)
        self.cover_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cover_label.setStyleSheet(
            "QLabel { background-color: #121212; border: 1px solid #404040; }"
        )
        layout.addWidget(self.cover_label)
        
        # Song title
        self.title_label = QLabel("No track selected")
        self.title_label.setFont(QFont("Arial", 14, QFont.Bold))
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.title_label)
        
        # Artist
        self.artist_label = QLabel("")
        self.artist_label.setFont(QFont("Arial", 10))
        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setWordWrap(True)
        self.artist_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.artist_label)
        
        layout.addStretch()
        
        self.setLayout(layout)
    
    def set_track_info(self, title, artist="Unknown Artist"):
        """Update display with track info"""
        self.title_label.setText(title)
        self.artist_label.setText(artist)
    
    def set_cover_art(self, pixmap):
        """Set cover art image"""
        if pixmap and not pixmap.isNull():
            self._cover_pixmap = pixmap
            self.cover_label.setText("")
            self._fit_cover_art()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_cover_size()
        self._fit_cover_art()

    def _update_cover_size(self):
        available_width = max(240, self.width() - 20)
        available_height = max(135, self.height() - 100)
        cover_width = min(available_width, int(available_height * 16 / 9))
        cover_height = max(135, int(cover_width * 9 / 16))
        self.cover_label.setFixedSize(cover_width, cover_height)

    def _fit_cover_art(self):
        if self._cover_pixmap.isNull():
            return

        width = self.cover_label.width()
        height = self.cover_label.height()
        if width <= 0 or height <= 0:
            return

        scaled = self._cover_pixmap.scaled(
            width,
            height,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x_offset = max(0, (scaled.width() - width) // 2)
        y_offset = max(0, (scaled.height() - height) // 2)
        self.cover_label.setPixmap(scaled.copy(x_offset, y_offset, width, height))

    def clear_cover_art(self):
        """Restore the cover art placeholder."""
        self._cover_pixmap = QPixmap()
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("🎵")
    
    def clear(self):
        """Clear the display"""
        self.title_label.setText("No track selected")
        self.artist_label.setText("")
        self.clear_cover_art()
