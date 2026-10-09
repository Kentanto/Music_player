from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Signal


class Sidebar(QWidget):
    """Navigation sidebar with Playlists only"""
    
    playlists_clicked = Signal()
    add_to_playlist_clicked = Signal()
    import_list_clicked = Signal()
    link_spotify_clicked = Signal()
    view_failures_clicked = Signal()
    
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

        self.import_list_btn = QPushButton("Import List")
        self.import_list_btn.clicked.connect(self.import_list_clicked.emit)
        layout.addWidget(self.import_list_btn)

        # Watch a linked Spotify playlist for new songs (checked at startup)
        self.link_spotify_btn = QPushButton("Link Spotify")
        self.link_spotify_btn.clicked.connect(self.link_spotify_clicked.emit)
        layout.addWidget(self.link_spotify_btn)

        # Show the failed_songs.txt error log for a playlist
        self.view_failures_btn = QPushButton("Failed Songs")
        self.view_failures_btn.clicked.connect(self.view_failures_clicked.emit)
        layout.addWidget(self.view_failures_btn)

        layout.addStretch()
        
        self.setLayout(layout)
