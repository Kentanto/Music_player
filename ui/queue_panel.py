import os
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QLineEdit, QComboBox, QMenu, QStyledItemDelegate, QStyle,
)
from PySide6.QtCore import QEvent, Qt, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap, QColor, QFont, QPen, QPainter


def _seconds_to_str(total_seconds):
    if not total_seconds or total_seconds <= 0:
        return ""
    m, s = divmod(int(total_seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class QueueItemDelegate(QStyledItemDelegate):
    """Rich row delegate: icon + title / artist / duration."""

    ICON_SIZE = 40
    PADDING = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self._default_icon = QIcon.fromTheme("audio-x-generic")
        self._thumb_cache = {}

    def _thumb_pixmap(self, thumbnail):
        if thumbnail in self._thumb_cache:
            return self._thumb_cache[thumbnail]
        if thumbnail and isinstance(thumbnail, str):
            if Path(thumbnail).exists():
                pix = QPixmap(thumbnail)
                if not pix.isNull():
                    self._thumb_cache[thumbnail] = pix
                    return pix
        self._thumb_cache[thumbnail] = None
        return None

    def paint(self, painter, option, index):
        painter.save()
        data = index.data(Qt.UserRole)
        is_playing = bool(index.data(Qt.UserRole + 1))
        is_queued_next = bool(index.data(Qt.UserRole + 2))

        bg = option.palette.base().color()
        if is_playing:
            bg = QColor("#1b5e20")
        elif is_queued_next:
            bg = QColor("#5d4037")
        elif option.state & QStyle.State_Selected:
            bg = QColor("#1db954")
        elif option.state & QStyle.State_MouseOver:
            bg = QColor("#282828")
        painter.fillRect(option.rect, bg)

        pen = QPen(QColor("#282828"))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())

        icon_rect = option.rect.adjusted(self.PADDING, self.PADDING, 0, -self.PADDING)
        icon_rect.setWidth(self.ICON_SIZE)
        icon_rect.setHeight(self.ICON_SIZE)

        if data and data.get("thumbnail"):
            thumb = self._thumb_pixmap(data["thumbnail"])
            if thumb:
                painter.drawPixmap(icon_rect, thumb.scaled(
                    self.ICON_SIZE, self.ICON_SIZE,
                    Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                ))
            else:
                painter.drawText(icon_rect, Qt.AlignCenter, "??")
        else:
            painter.drawText(icon_rect, Qt.AlignCenter, "??")

        text_x = icon_rect.right() + self.PADDING
        duration_w = 50

        title = str(data.get("title", "Unknown")) if data else "Unknown"
        if is_playing:
            title = "? " + title
        elif is_queued_next:
            title = "? " + title
        if data and data.get("count") is not None:
            title += f"  ({data.get('count', 0)} songs)"

        font = QFont("Arial", 9, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        title_rect = option.rect.adjusted(text_x, self.PADDING, -self.PADDING - duration_w, 0)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignTop, title)

        artist = str(data.get("artist", "")) if data else ""
        if artist:
            font.setPointSize(8)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor("#b3b3b3"))
            artist_rect = option.rect.adjusted(text_x, self.PADDING + 18, -self.PADDING - duration_w, 0)
            painter.drawText(artist_rect, Qt.AlignLeft | Qt.AlignTop, artist)

        duration = _seconds_to_str(data.get("duration") if data else None)
        if duration:
            font.setPointSize(8)
            painter.setFont(font)
            painter.setPen(QColor("#888888"))
            dur_rect = option.rect.adjusted(0, self.PADDING, -self.PADDING, 0)
            painter.drawText(dur_rect, Qt.AlignRight | Qt.AlignTop, duration)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ICON_SIZE + self.PADDING * 2)


class QueuePanel(QWidget):
    """Displays search results or queue with rich rows."""

    item_selected = Signal(object)
    item_previewed = Signal(object)
    item_double_clicked = Signal(object)
    queue_next_requested = Signal(object)
    add_to_playlist_requested = Signal(object)
    remove_requested = Signal(object)
    rename_requested = Signal(object)
    delete_playlist_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_items = []
        self.items_data = []
        self.preserve_order = False
        self.current_item_source = None
        self.queued_next_source = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Results / Queue")
        layout.addWidget(title)

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

        self.list_widget = QListWidget()
        self.list_widget.setItemDelegate(QueueItemDelegate(self.list_widget))
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.installEventFilter(self)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.list_widget.itemActivated.connect(self.on_item_double_clicked)
        self.list_widget.currentItemChanged.connect(self.on_current_item_changed)
        layout.addWidget(self.list_widget)

        self.setLayout(layout)

    def add_items(self, items, preserve_order=False, current_item_source=None):
        self.master_items = list(items)
        self.items_data = list(items)
        self.preserve_order = preserve_order
        self.current_item_source = current_item_source
        self._refresh_display()

    def set_playback_order(self, ordered_sources, current_source=None, queued_next=None):
        source_map = {}
        for item in self.master_items:
            src = item.get("file_path") or item.get("url")
            if src:
                source_map[src] = item

        new_order = []
        seen = set()
        for src in ordered_sources:
            if src in source_map and src not in seen:
                new_order.append(source_map[src])
                seen.add(src)
        for item in self.master_items:
            src = item.get("file_path") or item.get("url")
            if src not in seen:
                new_order.append(item)

        self.items_data = new_order
        self.current_item_source = current_source
        self.queued_next_source = queued_next
        self._refresh_display()

    def _refresh_display(self):
        scroll_bar = self.list_widget.verticalScrollBar()
        previous_scroll_value = scroll_bar.value()
        was_at_bottom = previous_scroll_value >= scroll_bar.maximum()

        filter_text = self.filter_input.text().strip().casefold()
        sort_mode = self.sort_combo.currentText()

        items = list(self.items_data)

        if sort_mode == "Title":
            items.sort(key=lambda x: str(x.get("title", "")).casefold())
        elif sort_mode == "Duration":
            items.sort(key=lambda x: x.get("duration") or float("inf"))
        elif sort_mode == "Date Added":
            items.sort(key=lambda x: x.get("added_at") or "")
        self.list_widget.setSortingEnabled(False)

        if filter_text:
            items = [item for item in items if filter_text in str(item.get("title", "")).casefold()]

        self.list_widget.clear()

        current_idx = -1
        for idx, item in enumerate(items):
            src = item.get("file_path") or item.get("url")
            list_item = QListWidgetItem()
            list_item.setData(Qt.UserRole, item)
            list_item.setData(Qt.UserRole + 1, src == self.current_item_source)
            list_item.setData(Qt.UserRole + 2, src == self.queued_next_source)
            list_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.list_widget.addItem(list_item)
            if src == self.current_item_source:
                current_idx = idx

        if current_idx >= 0:
            self.list_widget.setCurrentRow(current_idx)
        elif items:
            self.list_widget.setCurrentRow(0)

        if was_at_bottom:
            scroll_bar.setValue(scroll_bar.maximum())
        else:
            scroll_bar.setValue(previous_scroll_value)

    def get_current_index(self):
        return self.list_widget.currentRow()

    def get_current_item(self):
        current = self.list_widget.currentItem()
        if current is not None:
            return current.data(Qt.UserRole)
        return None

    def get_all_urls(self):
        return [item.get("file_path") or item.get("url") for item in self.items_data]

    def on_item_clicked(self, item):
        self.item_selected.emit(item.data(Qt.UserRole))

    def on_current_item_changed(self, current, previous):
        if current is None:
            return
        self.item_previewed.emit(current.data(Qt.UserRole))

    def eventFilter(self, watched, event):
        if watched is self.list_widget and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                current = self.list_widget.currentItem()
                if current is not None:
                    self.item_double_clicked.emit(current.data(Qt.UserRole))
                    return True
        return super().eventFilter(watched, event)

    def on_item_double_clicked(self, item):
        self.item_double_clicked.emit(item.data(Qt.UserRole))

    def remove_item_by_url(self, url):
        for i, item in enumerate(self.items_data):
            if (item.get("file_path") or item.get("url")) == url:
                self.items_data.pop(i)
                self.list_widget.takeItem(i)
                return

    def _show_context_menu(self, point):
        item = self.list_widget.itemAt(point)
        if item is None:
            return

        data = item.data(Qt.UserRole)
        if data is None:
            return

        self.list_widget.setCurrentItem(item)
        try:
            self.item_selected.emit(data)
        except Exception:
            pass

        menu = QMenu(self.list_widget)
        add_to_playlist_action = None
        if data.get("type") != "playlist":
            add_to_playlist_action = menu.addAction("Add to Playlist")
        queue_next_action = menu.addAction("Queue Next")
        remove_action = None
        rename_action = menu.addAction("Rename Playlist" if data.get("type") == "playlist" else "Rename Song")
        delete_playlist_action = None
        if data.get("type") == "playlist":
            delete_playlist_action = menu.addAction("Delete Playlist")
        if data.get("type") == "track" and data.get("file_path"):
            remove_action = menu.addAction("Remove and Delete File")
        action = menu.exec_(self.list_widget.mapToGlobal(point))
        if action == add_to_playlist_action:
            self.add_to_playlist_requested.emit(data)
        elif action == queue_next_action:
            self.queue_next_requested.emit(data)
        elif action == rename_action:
            self.rename_requested.emit(data)
        elif action == delete_playlist_action:
            self.delete_playlist_requested.emit(data)
        elif action == remove_action:
            self.remove_requested.emit(data)
