from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Signal


class Sidebar(QWidget):
    """Navigation sidebar with Playlists only"""
    
    playlists_clicked = Signal()
    add_to_playlist_clicked = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Playlists button
        self.playlists_btn = QPushButton("📋 Playlists")
        self.playlists_btn.clicked.connect(self.playlists_clicked.emit)
        layout.addWidget(self.playlists_btn)

        # Add to playlist
        self.add_to_playlist_btn = QPushButton("➕ Add to Playlist")
        self.add_to_playlist_btn.clicked.connect(self.add_to_playlist_clicked.emit)
        layout.addWidget(self.add_to_playlist_btn)

        layout.addStretch()
        
        self.setLayout(layout)
