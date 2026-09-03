from pathlib import Path
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QLabel, QLineEdit, QComboBox, QMenu
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
import random


class QueuePanel(QWidget):
    """Displays search results or queue"""
    
    item_selected = Signal(object)  # Emitted when user selects an item
    item_double_clicked = Signal(object)  # Emitted when user double-clicks an item
    queue_next_requested = Signal(object)  # Emitted when user wants to queue a track next
    remove_requested = Signal(object)  # Emitted when a playlist track should be removed
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_items = []
        self.items_data = []  # Store visible item data
        self.preserve_order = False
        self.current_item_source = None
        self.current_item_index = -1
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Title
        title = QLabel("Results / Queue")
        layout.addWidget(title)
        
        # Filter and sort controls
        controls = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter songs...")
        self.filter_input.textChanged.connect(self._refresh_display)
        controls.addWidget(self.filter_input)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort by", "Title", "Duration", "Date Added", "Shuffled"])
        self.sort_combo.model().item(0).setEnabled(False)
        self.sort_combo.setCurrentText("Date Added")
        self.sort_combo.currentIndexChanged.connect(self._refresh_display)
        controls.addWidget(self.sort_combo)

        layout.addLayout(controls)

        # List widget
        self.list_widget = QListWidget()
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        layout.addWidget(self.list_widget)
        
        self.setLayout(layout)
    
    def add_items(self, items, preserve_order=False, current_item_source=None):
        """Add items to the queue. Items should be dicts with 'title', 'url', 'duration', etc."""
        self.master_items = list(items)
        self.preserve_order = preserve_order
        self.current_item_source = current_item_source
        self.current_item_index = -1
        self._refresh_display()

    def _refresh_display(self):
        filter_text = self.filter_input.text().strip().casefold()
        sort_text = self.sort_combo.currentText()

        items = [item for item in self.master_items if filter_text in (item.get("title", "").casefold())]

        if sort_text == "Title":
            items.sort(key=lambda item: (item.get("title") or "").casefold())
        elif sort_text == "Duration":
            items.sort(key=lambda item: item.get("duration") or 0)
        elif sort_text == "Date Added":
            def date_key(item):
                if item.get("date_added") is not None:
                    return item.get("date_added")
                if item.get("file_path"):
                    try:
                        return __import__("pathlib").Path(item.get("file_path")).stat().st_mtime
                    except Exception:
                        return 0
                return 0
            items.sort(key=date_key, reverse=True)
        elif sort_text == "Shuffled":
            items = list(items)
            if getattr(self, "shuffle_order", None):
                ordered = []
                for source in self.shuffle_order:
                    for item in items:
                        item_source = item.get("file_path") or item.get("url")
                        if item_source == source:
                            ordered.append(item)
                            break
                items = ordered + [item for item in items if (item.get("file_path") or item.get("url")) not in self.shuffle_order]
            else:
                random.shuffle(items)

        if self.preserve_order:
            items = list(items)

        self.items_data = items
        self.list_widget.clear()

        for item in items:
            title = item.get("title", "Unknown")
            duration = item.get("duration")
            item_type = item.get("type")
            
            if item_type == "playlist":
                count = item.get("count", 0)
                display_text = f"{title} ({count} songs)"
            elif duration is not None:
                mins = duration // 60
                secs = duration % 60
                display_text = f"{title} ({mins}:{secs:02d})"
            else:
                display_text = title
            
            list_item = QListWidgetItem(display_text)
            source = item.get("file_path") or item.get("url")
            if self._matches_current_source(item):
                list_item.setBackground(QColor("#000000"))
                list_item.setForeground(QColor("#1db954"))
                font = list_item.font()
                font.setBold(True)
                list_item.setFont(font)
            self.list_widget.addItem(list_item)
    
    def _matches_current_source(self, item):
        current_source = self.current_item_source
        if current_source is None:
            return False

        for candidate in (item.get("file_path"), item.get("url")):
            if candidate is not None and str(candidate) == str(current_source):
                return True
        return False

    def clear(self):
        self.list_widget.clear()
        self.master_items = []
        self.items_data = []
        self.filter_input.clear()
        self.sort_combo.setCurrentText("Date Added")
    
    def get_current_index(self):
        return self.list_widget.currentRow()
    
    def get_current_item(self):
        idx = self.get_current_index()
        if 0 <= idx < len(self.items_data):
            return self.items_data[idx]
        return None
    
    def get_all_urls(self):
        """Get all playback sources in current queue"""
        return [item.get("file_path") or item.get("url") for item in self.items_data]
    
    def on_item_clicked(self, item):
        idx = self.list_widget.row(item)
        self.item_selected.emit(self.items_data[idx])
    
    def on_item_double_clicked(self, item):
        idx = self.list_widget.row(item)
        self.item_double_clicked.emit(self.items_data[idx])
    
    def remove_item_by_url(self, url):
        """Remove an item from the queue by its URL"""
        for i, item in enumerate(self.items_data):
            if (item.get("file_path") or item.get("url")) == url:
                self.items_data.pop(i)
                self.list_widget.takeItem(i)
                return

    def _show_context_menu(self, point):
        item = self.list_widget.itemAt(point)
        if item is None:
            return

        idx = self.list_widget.row(item)
        if idx < 0 or idx >= len(self.items_data):
            return

        data = self.items_data[idx]
        if data is None:
            return

        # Auto-select the item on right-click so the UI reflects the action
        self.list_widget.setCurrentRow(idx)
        try:
            self.item_selected.emit(data)
        except Exception:
            # If no handler is connected, continue silently
            pass

        menu = QMenu(self.list_widget)
        queue_next_action = menu.addAction("Queue Next")
        remove_action = None
        if data.get("type") == "track" and data.get("file_path"):
            remove_action = menu.addAction("Remove and Delete File")
        action = menu.exec_(self.list_widget.mapToGlobal(point))
        if action == queue_next_action:
            self.queue_next_requested.emit(data)
        elif action == remove_action:
            self.remove_requested.emit(data)
