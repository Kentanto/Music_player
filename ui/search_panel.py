from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit
from PySide6.QtCore import Signal


class SearchPanel(QWidget):
    """Search input panel for YouTube queries"""
    
    search_requested = Signal(str)  # Emitted when user clicks search
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Search bar
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search YouTube...")
        self.search_bar.returnPressed.connect(self.on_search)
        layout.addWidget(self.search_bar)
        
        self.setLayout(layout)
    
    def on_search(self):
        query = self.search_bar.text().strip()
        if query:
            self.search_requested.emit(query)
    
    def get_query(self):
        return self.search_bar.text().strip()
    
    def clear(self):
        self.search_bar.clear()
