from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap


class CoverWidget(QWidget):
    """Displays current track: cover art, title, artist"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setAlignment(Qt.AlignCenter)
        
        # Cover art placeholder
        self.cover_label = QLabel("🎵")
        self.cover_label.setFont(QFont("Arial", 80))
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(200, 200)
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
        if pixmap:
            scaled = pixmap.scaledToWidth(200, Qt.SmoothTransformation)
            self.cover_label.setPixmap(scaled)
    
    def clear(self):
        """Clear the display"""
        self.title_label.setText("No track selected")
        self.artist_label.setText("")
        self.cover_label.setPixmap(QPixmap())
