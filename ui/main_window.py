from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut, QKeySequence



from .sidebar import Sidebar
from .search_panel import SearchPanel
from .queue_panel import QueuePanel
from .player_bar import PlayerBar
from .cover_widget import CoverWidget


class MainWindow(QMainWindow):
    """Main application window with sidebar, search, queue, player controls"""
    
    # Signals for business logic
    search_requested = Signal(str)
    play_pause_track = Signal()
    play_track_index = Signal(object)  # Play specific track by index
    queue_next_requested = Signal(object)
    next_track = Signal()
    prev_track = Signal()
    shuffle_toggled = Signal(bool)
    load_playlists = Signal()
    add_to_playlist = Signal()
    import_list_requested = Signal()
    open_playlist_requested = Signal(int)
    volume_changed = Signal(int)
    seek_requested = Signal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Music Engine")
        self.resize(1200, 800)
        
        self.init_ui()
        self.connect_signals()
    
    def init_ui(self):
        """Initialize the main UI"""
        central = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # ===== TOP: Search and main content area =====
        top_layout = QHBoxLayout()
        
        # Left: Sidebar
        self.sidebar = Sidebar()
        top_layout.addWidget(self.sidebar, 0)
        
        # Middle: Search results / Queue
        middle_layout = QVBoxLayout()
        
        self.search_panel = SearchPanel()
        middle_layout.addWidget(self.search_panel, 0)
        
        # Center area with cover art and queue
        center_splitter = QSplitter(Qt.Horizontal)
        
        self.cover_widget = CoverWidget()
        center_splitter.addWidget(self.cover_widget)
        
        self.queue_panel = QueuePanel()
        center_splitter.addWidget(self.queue_panel)
        
        center_splitter.setSizes([300, 400])
        middle_layout.addWidget(center_splitter, 1)
        
        top_layout.addLayout(middle_layout, 1)
        
        main_layout.addLayout(top_layout, 1)
        
        # ===== BOTTOM: Player controls =====
        self.player_bar = PlayerBar()
        main_layout.addWidget(self.player_bar, 0)
        
        central.setLayout(main_layout)
        self.setCentralWidget(central)
    
    def connect_signals(self):
        """Connect UI signals to business logic signals"""
        # Search
        self.search_panel.search_requested.connect(self.search_requested.emit)
        
        # Queue selection
        self.queue_panel.item_selected.connect(self.on_queue_item_selected)
        self.queue_panel.item_double_clicked.connect(self.on_queue_item_double_clicked)
        self.queue_panel.queue_next_requested.connect(self.queue_next_requested.emit)
        
        # Player controls
        self.player_bar.play_pause_clicked.connect(self.play_pause_track.emit)
        self.player_bar.next_clicked.connect(self.next_track.emit)
        self.player_bar.prev_clicked.connect(self.prev_track.emit)
        self.player_bar.shuffle_toggled.connect(self.shuffle_toggled.emit)
        self.player_bar.volume_changed.connect(self.volume_changed.emit)
        self.player_bar.seek_requested.connect(self.seek_requested.emit)
        
        # Sidebar
        self.sidebar.add_to_playlist_clicked.connect(self.add_to_playlist.emit)
        self.sidebar.import_list_clicked.connect(self.import_list_requested.emit)
        self.sidebar.playlists_clicked.connect(self.load_playlists.emit)

        QShortcut(QKeySequence(Qt.Key_MediaPlay), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaPause), self).activated.connect(self.play_pause_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaNext), self).activated.connect(self.next_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaPrevious), self).activated.connect(self.prev_track.emit)
        QShortcut(QKeySequence(Qt.Key_MediaTogglePlayPause), self).activated.connect(self.play_pause_track.emit)
    
    def on_queue_item_selected(self, item):
        """Handle queue item selection"""
        self.cover_widget.set_track_info(item.get("title", "Unknown"))

    def on_queue_item_double_clicked(self, item):
        if item.get("type") == "playlist":
            self.open_playlist_requested.emit(item.get("playlist_id"))
        else:
            self.play_track_index.emit(item)
    
    def display_results(self, results, preserve_order=False, current_item_source=None):
        """Display search results in queue panel"""
        self.queue_panel.add_items(results, preserve_order=preserve_order, current_item_source=current_item_source)
    
    def display_library(self, songs, preserve_order=False, current_item_source=None):
        """Display library songs"""
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
        """Display playlist list in the queue panel"""
        self.queue_panel.add_items(playlists, preserve_order=preserve_order, current_item_source=current_item_source)
    
    def get_current_queue_urls(self):
        """Get all URLs in current queue"""
        return self.queue_panel.get_all_urls()
    
    def get_current_track_index(self):
        """Get currently selected track index"""
        return self.queue_panel.get_current_index() # LEGACY ONLY
    
    def update_player_time(self, current, total):
        """Update player time display"""
        self.player_bar.set_time_label(current, total)
    
    def set_volume(self, value):
        """Update volume slider"""
        self.player_bar.set_volume(value)
