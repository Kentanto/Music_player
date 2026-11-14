import os
from pathlib import Path
from PySide6.QtWidgets import (
    QAbstractItemView, QWidget, QApplication, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QLineEdit, QComboBox, QPushButton, QMenu, QStyledItemDelegate, QStyle,
)
from PySide6.QtCore import QEvent, Qt, Signal, QSize, QUrl, QTimer, QPoint
from PySide6.QtGui import QIcon, QPixmap, QColor, QFont, QPen, QPainter
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


def _seconds_to_str(total_seconds):
    if not total_seconds or total_seconds <= 0:
        return ""
    m, s = divmod(int(total_seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class ThumbnailLoader(QNetworkAccessManager):
    """Async thumbnail downloader for remote URLs; local files are loaded directly."""

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ThumbnailLoader()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_cache = {}
        self._in_flight = {}
        self._callbacks = {}
        self.finished.connect(self._on_reply_finished)

    def request_thumb(self, url, list_widget):
        if url in self._thumb_cache:
            return self._thumb_cache[url]
        if url in self._in_flight:
            self._in_flight[url].append(list_widget)
            return None
        if not url or not isinstance(url, str):
            return None
        # Local file — load immediately
        if Path(url).exists():
            pix = QPixmap(url)
            if not pix.isNull():
                self._thumb_cache[url] = pix
                return pix
            return None
        # Remote URL — queue download
        self._in_flight[url] = [list_widget]
        req = QNetworkRequest(QUrl(url))
        self.get(req)
        return None

    def fetch_async(self, url, callback):
        """Fetch a remote thumbnail for a non-list target (cover art) and
        call callback(QPixmap or None) when done, so the UI thread never
        blocks on a network download."""
        if not url or not isinstance(url, str):
            callback(None)
            return
        cached = self._thumb_cache.get(url)
        if cached is not None:
            callback(cached)
            return
        self._callbacks.setdefault(url, []).append(callback)
        if url not in self._in_flight:
            self._in_flight[url] = []
            req = QNetworkRequest(QUrl(url))
            self.get(req)

    def _on_reply_finished(self, reply: QNetworkReply):
        url = reply.url().toString()
        widgets = self._in_flight.pop(url, [])
        callbacks = self._callbacks.pop(url, [])
        if reply.error() == QNetworkReply.NoError:
            data = reply.readAll()
            if data:
                pix = QPixmap()
                if pix.loadFromData(data):
                    self._thumb_cache[url] = pix
                    for widget in widgets:
                        widget.viewport().update()
                    for callback in callbacks:
                        callback(pix)
                    reply.deleteLater()
                    return
        for callback in callbacks:
            callback(None)
        reply.deleteLater()

    @classmethod
    def get_thumb(cls, url, list_widget):
        return cls.instance().request_thumb(url, list_widget)

    @classmethod
    def has_thumb(cls, url):
        return url in cls.instance()._thumb_cache

    @classmethod
    def cached_pixmap(cls, url):
        return cls.instance()._thumb_cache.get(url)


class QueueItemDelegate(QStyledItemDelegate):
    """Rich row delegate: icon + title / artist / duration.

    Row size comes from a base size (normal or touch) multiplied by a
    user-chosen scale factor (settings dialog), so songs can be drawn
    bigger or smaller without touching the layout code.
    """

    BASE_ICON_SIZE = 40
    BASE_PADDING = 6
    BASE_TOUCH_ICON_SIZE = 110
    BASE_TOUCH_PADDING = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._default_icon = QIcon.fromTheme("audio-x-generic")
        self._thumb_cache = {}
        self._touch_mode = False
        self._scale = 1.0
        self.ICON_SIZE = self.BASE_ICON_SIZE
        self.PADDING = self.BASE_PADDING

    def _apply_metrics(self):
        """Recompute row metrics from the current mode + scale."""
        if self._touch_mode:
            self.ICON_SIZE = max(12, int(self.BASE_TOUCH_ICON_SIZE * self._scale))
            self.PADDING = max(2, int(self.BASE_TOUCH_PADDING * self._scale))
        else:
            self.ICON_SIZE = max(12, int(self.BASE_ICON_SIZE * self._scale))
            self.PADDING = max(1, int(self.BASE_PADDING * self._scale))
        parent = self.parent()
        if parent:
            parent.viewport().update()

    def set_touch_mode(self, enabled):
        self._touch_mode = bool(enabled)
        self._apply_metrics()

    def set_scale(self, scale):
        """Set the row size scale factor (1.0 = default size)."""
        try:
            self._scale = max(0.5, min(3.0, float(scale)))
        except (TypeError, ValueError):
            self._scale = 1.0
        self._apply_metrics()

    def scale(self):
        return self._scale

    def _thumb_pixmap(self, thumbnail, list_widget):
        if thumbnail in self._thumb_cache:
            return self._thumb_cache[thumbnail]
        if thumbnail and isinstance(thumbnail, str):
            if Path(thumbnail).exists():
                pix = QPixmap(thumbnail)
                if not pix.isNull():
                    self._thumb_cache[thumbnail] = pix
                    return pix
            # Remote URL — try async loader
            pix = ThumbnailLoader.get_thumb(thumbnail, list_widget)
            if pix:
                self._thumb_cache[thumbnail] = pix
                return pix
        self._thumb_cache[thumbnail] = None
        return None

    @staticmethod
    def _is_selected(option, index, list_widget):
        if option.state & QStyle.State_Selected:
            return True
        if list_widget and list_widget.selectionModel():
            return list_widget.selectionModel().isSelected(index)
        return False

    def paint(self, painter, option, index):
        painter.save()
        data = index.data(Qt.UserRole)
        is_playing = bool(index.data(Qt.UserRole + 1))
        is_queued_next = bool(index.data(Qt.UserRole + 2))
        list_widget = self.parent()
        selected = self._is_selected(option, index, list_widget)

        bg = option.palette.base().color()
        if is_playing:
            bg = QColor("#1b5e20")
        elif is_queued_next:
            bg = QColor("#5d4037")
        elif selected:
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

        list_widget = self.parent()
        if data and data.get("thumbnail"):
            thumb = self._thumb_pixmap(data["thumbnail"], list_widget)
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

        title = str(data.get("title") or "Unknown") if data else "Unknown"
        if data and data.get("count") is not None:
            title += f"  ({data.get('count', 0)} songs)"

        # Fonts and the duration column scale with the same user-chosen
        # factor as the icon/padding, in both normal and touch mode.
        base_title_px = 30 if self._touch_mode else 12
        base_sub_px = 24 if self._touch_mode else 10
        base_duration_w = 120 if self._touch_mode else 50
        title_px = max(6, int(base_title_px * self._scale))
        sub_px = max(5, int(base_sub_px * self._scale))
        duration_w = max(30, int(base_duration_w * self._scale))

        font = QFont("Arial")
        font.setPixelSize(title_px)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        title_rect = option.rect.adjusted(text_x, self.PADDING, -self.PADDING - duration_w, 0)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignTop, title)

        artist = str(data.get("artist") or "") if data else ""
        if artist:
            font.setPixelSize(sub_px)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor("#b3b3b3"))
            artist_rect = option.rect.adjusted(text_x, self.PADDING + self.ICON_SIZE // 2, -self.PADDING - duration_w, 0)
            painter.drawText(artist_rect, Qt.AlignLeft | Qt.AlignTop, artist)

        duration = _seconds_to_str(data.get("duration") if data else None)
        if duration:
            font.setPixelSize(sub_px)
            painter.setFont(font)
            painter.setPen(QColor("#888888"))
            dur_rect = option.rect.adjusted(0, self.PADDING, -self.PADDING, 0)
            painter.drawText(dur_rect, Qt.AlignRight | Qt.AlignTop, duration)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ICON_SIZE + self.PADDING * 2 + int(8 * self._scale))


class _ScrollBadge(QWidget):
    """Floating pill that mirrors — and can drive — the list's scroll position.

    Replaces the classic scrollbar: no track/column is reserved for it.
    In touch mode it is a grabbable handle the finger can drag to scroll
    the list; in normal mode it stays passive and see-through to input.
    Either way it only appears while the list is actually being scrolled
    and hides itself again once scrolling stops.
    """

    drag_started = Signal()
    drag_finished = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._w = 8
        self._min_handle = 36
        self._list_widget = None
        self._track_h = 0
        self._handle_h = 0
        self._dragging = False
        self._press_y = 0.0
        self._press_value = 0
        self.setFixedWidth(self._w)
        self.hide()

    def set_width(self, w):
        self._w = int(w)
        self.setFixedWidth(self._w)

    def set_min_handle(self, h):
        self._min_handle = int(h)

    def min_handle(self):
        return self._min_handle

    def set_list_widget(self, list_widget):
        self._list_widget = list_widget

    def is_dragging(self):
        return self._dragging

    def _scrollbar(self):
        if self._list_widget is None:
            return None
        try:
            return self._list_widget.verticalScrollBar()
        except RuntimeError:
            return None  # the list was destroyed

    def reposition(self, viewport, handle_h, y):
        """Anchor the badge to the right edge of the list's viewport."""
        self._track_h = viewport.height()
        self._handle_h = int(handle_h)
        self.setFixedHeight(max(28, int(handle_h)))
        origin = viewport.mapTo(self.parentWidget(), QPoint(0, 0))
        self.move(origin.x() + viewport.width() - self._w - 3, int(y))
        # The list widget is re-parented by the layout after this badge is
        # created, which stacks it ON TOP of the badge; raise so the badge
        # always floats above the list content.
        self.raise_()

    # ---------- dragging the badge scrolls the list ----------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        sb = self._scrollbar()
        if sb is None:
            event.ignore()
            return
        self._dragging = True
        self._press_y = event.globalPosition().y()
        self._press_value = sb.value()
        self.setCursor(Qt.ClosedHandCursor)
        self.drag_started.emit()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            event.ignore()
            return
        sb = self._scrollbar()
        if sb is None:
            return
        travel = self._track_h - self._handle_h
        if travel <= 0:
            return
        dy = event.globalPosition().y() - self._press_y
        span = max(1, sb.maximum())
        new_value = self._press_value + (dy / travel) * span
        sb.setValue(max(0, min(span, int(round(new_value)))))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self._dragging:
            event.ignore()
            return
        self._dragging = False
        self.setCursor(Qt.OpenHandCursor)
        self.drag_finished.emit()
        event.accept()

    def wheelEvent(self, event):
        # Keep mouse-wheel scrolling working when the cursor happens to be
        # over the badge: hand the wheel straight to the list below.
        if self._list_widget is not None:
            QApplication.sendEvent(self._list_widget.viewport(), event)
            event.accept()
        else:
            event.ignore()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(96, 96, 96, 230))
        r = min(self.width(), self.height()) / 2.0
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), r, r)


class QueuePanel(QWidget):
    """Displays search results or queue with rich rows."""

    item_selected = Signal(object)
    item_previewed = Signal(object)
    item_double_clicked = Signal(object)
    queue_next_requested = Signal(object)
    add_to_playlist_requested = Signal(object)
    remove_requested = Signal(object)
    soft_remove_requested = Signal(object)
    rename_requested = Signal(object)
    delete_playlist_requested = Signal(object)
    redownload_requested = Signal(object)
    redownload_all_requested = Signal(object)
    playback_order_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.master_items = []
        self.items_data = []
        self.preserve_order = False
        self.current_item_source = None
        self.queued_next_source = None
        self._reset_scroll_on_refresh = False
        self._commit_index = 2  # "Date Added"
        self._reverse = True
        self._touch_mode = False
        self.init_ui()

    def set_touch_mode(self, enabled):
        self._touch_mode = bool(enabled)
        delegate = self.list_widget.itemDelegate()
        if isinstance(delegate, QueueItemDelegate):
            delegate.set_touch_mode(enabled)
        self.filter_input.setMinimumHeight(52 if enabled else 28)
        self.sort_combo.setMinimumHeight(52 if enabled else 28)
        self.sort_dir_btn.setFixedSize(64 if enabled else 28, 52 if enabled else 28)
        # Floating scroll badge: twice as thick and with a longer grab
        # length in touch mode, slim in normal mode.
        self.scroll_badge.set_width(56 if enabled else 8)
        self.scroll_badge.set_min_handle(80 if enabled else 36)
        # Touch mode turns the badge into a real scrollbar handle the
        # finger can grab and drag. Normal mode keeps it see-through to
        # mouse/touch input exactly as before.
        self.scroll_badge.setAttribute(Qt.WA_TransparentForMouseEvents, not enabled)
        self.scroll_badge.setCursor(Qt.OpenHandCursor if enabled else Qt.ArrowCursor)
        # Either way the badge stays hidden until the list is actually
        # scrolled; drop any currently visible one and let it come back
        # with the new size on the next scroll.
        self.scroll_badge.hide()
        # Force re-layout of all rows
        self._refresh_display()

    def set_row_scale(self, scale):
        """Scale the song rows (icon, padding, fonts); 1.0 = default size.

        Works in both normal and touch mode — the factor multiplies the
        mode's base size, so touch rows stay proportionally bigger.
        """
        delegate = self.list_widget.itemDelegate()
        if isinstance(delegate, QueueItemDelegate):
            delegate.set_scale(scale)
        # Force re-layout of all rows so the new row height takes effect
        self._refresh_display()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Results / Queue")
        layout.addWidget(title)

        controls = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter songs...")
        self.filter_input.textChanged.connect(lambda _: self._refresh_display())
        controls.addWidget(self.filter_input)

        sort_layout = QHBoxLayout()
        sort_layout.setSpacing(2)

        self.sort_combo = QComboBox()
        self._base_sort_labels = ["Title", "Artist", "Date Added", "Shuffled"]
        self.sort_combo.addItems(self._base_sort_labels)
        self.sort_combo.setCurrentIndex(self._commit_index)
        self.sort_combo.activated.connect(self._on_sort_activated)
        sort_layout.addWidget(self.sort_combo)

        self.sort_dir_btn = QPushButton()
        self.sort_dir_btn.setFixedWidth(28)
        self.sort_dir_btn.setToolTip("Toggle ascending / descending")
        self.sort_dir_btn.clicked.connect(self._toggle_sort_reverse)
        self._update_dir_button_state()
        sort_layout.addWidget(self.sort_dir_btn)

        controls.addLayout(sort_layout)
        layout.addLayout(controls)

        self.list_widget = QListWidget()
        self.list_widget.setItemDelegate(QueueItemDelegate(self.list_widget))
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        # Pixel-accurate scrolling (not whole-row jumps): the touch drag
        # scroll in ui/touch_gestures.py can then track the finger 1:1,
        # and mouse-wheel scrolling becomes smoother too.
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # No scrollbar column at all: the list uses the full width and a
        # floating badge (self.scroll_badge) indicates the scroll position.
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)

        # Overlay scroll badge: appears over the content while scrolling and
        # hides itself after 1.5s of no activity. In touch mode it doubles
        # as a grabbable scrollbar handle (see set_touch_mode).
        self.scroll_badge = _ScrollBadge(self)
        self.scroll_badge.set_list_widget(self.list_widget)
        sb = self.list_widget.verticalScrollBar()
        self._scroll_hide_timer = QTimer(self)
        self._scroll_hide_timer.setInterval(1500)
        self._scroll_hide_timer.setSingleShot(True)
        self._scroll_hide_timer.timeout.connect(self._hide_scrollbar)
        # A grabbed badge must never vanish under the finger: pause the
        # auto-hide while it is held, give it a fresh countdown on release.
        self.scroll_badge.drag_started.connect(self._scroll_hide_timer.stop)
        self.scroll_badge.drag_finished.connect(self._scroll_hide_timer.start)
        sb.valueChanged.connect(self._on_scroll_activity)
        sb.rangeChanged.connect(lambda *_: self._update_scroll_badge())

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
        self._reset_scroll_on_refresh = True
        self._refresh_display()

    def update_item_data(self, url, data):
        """Refresh one row's data in place (metadata enrichment).

        QListWidgetItem keeps a *copy* of the item dict, so in-place
        mutations are not picked up — rewrite just that row's data and
        repaint.  No clear/re-add: selection, hover and scroll position
        stay untouched, so browsing/clicking is never interrupted.
        """
        if not url:
            return
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            existing = item.data(Qt.UserRole)
            if not existing or (existing.get("url") != url and existing.get("file_path") != url):
                continue
            item.setData(Qt.UserRole, data)
            break
        self.list_widget.viewport().update()

    def set_playback_order(self, ordered_sources, current_source=None, queued_next=None, reset_scroll=False):
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
        if reset_scroll:
            self._reset_scroll_on_refresh = True
        self._refresh_display()

    def set_sort_mode(self, mode, reverse=False):
        """Programmatically set the sort mode and sync internal state."""
        base = mode.rstrip(" \u25b2\u25bc\u2191\u2193")
        self._reverse = reverse
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentText(base)
        self.sort_combo.blockSignals(False)
        self._commit_index = self.sort_combo.currentIndex()
        self._update_dir_button_state()
        self._refresh_display()

    def _update_dir_button_state(self):
        """Enable/disable the direction button and sync its glyph."""
        sort_mode = self._base_sort_labels[self._commit_index]
        is_shuffled = sort_mode == "Shuffled"
        self.sort_dir_btn.setEnabled(not is_shuffled)
        self.sort_dir_btn.setText("\u2193" if self._reverse else "\u2191")

    def _toggle_sort_reverse(self):
        """Toggle ascending/descending when the arrow button is clicked."""
        if not self.sort_dir_btn.isEnabled():
            return
        self._reverse = not self._reverse
        self._update_dir_button_state()
        self._refresh_display(True)

    def _on_sort_activated(self, index):
        """Committed selection from the dropdown."""
        if index < 0 or index >= len(self._base_sort_labels):
            return
        self._commit_index = index
        self._update_dir_button_state()
        self._refresh_display(True)

    def _refresh_display(self, from_sort_widget=False):
        scroll_bar = self.list_widget.verticalScrollBar()
        previous_scroll_value = scroll_bar.value()
        # A scrollbar that has never been laid out reports 0/0 — that must
        # not count as "the user was at the bottom" (a fresh view used to
        # open at the bottom of the list because of this).
        was_at_bottom = (
            scroll_bar.maximum() > 0
            and previous_scroll_value >= scroll_bar.maximum()
        )
        reset_scroll = getattr(self, "_reset_scroll_on_refresh", False)
        if reset_scroll:
            self._reset_scroll_on_refresh = False

        filter_text = self.filter_input.text().strip().casefold()
        sort_index = self._commit_index
        sort_mode = self._base_sort_labels[sort_index] if 0 <= sort_index < len(self._base_sort_labels) else "Title"

        items = list(self.items_data)

        if sort_mode == "Title":
            items.sort(key=lambda x: str(x.get("title", "")).casefold(), reverse=self._reverse)
        elif sort_mode == "Artist":
            items.sort(key=lambda x: str(x.get("artist", "")).casefold(), reverse=self._reverse)
        elif sort_mode == "Date Added":
            items.sort(key=lambda x: x.get("added_at") or "", reverse=self._reverse)
        elif sort_mode == "Shuffled":
            pass
        self.list_widget.setSortingEnabled(False)

        # Persist the visual ordering so the player's queue can stay in sync
        self.items_data = list(items)

        if filter_text:
            # Match the artist name as well, so filtering works no matter
            # which of the two the user remembers.
            items = [
                item
                for item in items
                if filter_text in str(item.get("title", "")).casefold()
                or filter_text in str(item.get("artist", "")).casefold()
            ]

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

        # Highlight the playing row WITHOUT letting QListWidget scroll to it.
        # Qt's built-in current-item handling scrolls the view to the current
        # row — and when the panel is not visible yet, it makes the view jump
        # there the moment the window is shown. That is what kept yanking the
        # view to the playing song instead of leaving the user where they
        # were. The delegate paints selection from the selection model state
        # directly, so blocking the signals does not affect how rows look.
        if current_idx >= 0:
            selection_model = self.list_widget.selectionModel()
            selection_model.blockSignals(True)
            self.list_widget.setCurrentRow(current_idx)
            selection_model.blockSignals(False)
            self.list_widget.viewport().update()
        else:
            self.list_widget.clearSelection()
            self.list_widget.viewport().update()

        # Force the rows to lay out NOW. Right after clear()/re-add the
        # scrollbar range is stale (it only covers about half the rows), and
        # positioning against it is what made the view "teleport back to the
        # middle" after a delete. scrollToBottom() makes Qt run the full
        # layout + geometry update first, so the scrollbar range is final
        # before we place it where it belongs.
        # Block signals so the auto-hide scrollbar does not flash on refresh.
        scroll_bar.blockSignals(True)
        try:
            self.list_widget.scrollToBottom()
            if reset_scroll:
                # New view (playlist opened): always start at the top. The layout
                # is already final, so this can be done synchronously.
                self.list_widget.scrollToTop()
            elif not was_at_bottom:
                # Keep the user where they were; scrollToBottom above already
                # handled the "user was at the bottom" case.
                scroll_bar.setValue(previous_scroll_value)
        finally:
            scroll_bar.blockSignals(False)

        # The signal block above kept the floating badge from hearing the
        # scrollbar changes, so bring it back in sync right away — it only
        # becomes visible again on the next actual scroll.
        self._update_scroll_badge()

        # Notify the controller whenever the user causes a re-order so
        # the playback queue stays synchronized with the visible list.
        if from_sort_widget:
            urls = [i.get("file_path") or i.get("url") for i in self.items_data]
            self.playback_order_changed.emit(urls)

    def get_current_index(self):
        return self.list_widget.currentRow()

    def get_current_item(self):
        current = self.list_widget.currentItem()
        if current is not None:
            return current.data(Qt.UserRole)
        return None

    def get_all_urls(self):
        return [item.get("file_path") or item.get("url") for item in self.items_data]

    def scroll_to_current_track(self):
        """Scroll the queue list so the currently playing song's row is
        centered in the view.

        Returns True when the playing track was found (and scrolled to),
        False when nothing is playing or the track is not in the current
        view (e.g. a different playlist/search result is shown).
        """
        source = self.current_item_source
        if not source:
            return False
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            data = item.data(Qt.UserRole) if item else None
            if not data:
                continue
            if (data.get("file_path") or data.get("url")) == source:
                self.list_widget.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter
                )
                return True
        return False

    def on_item_clicked(self, item):
        self.item_selected.emit(item.data(Qt.UserRole))

    def on_current_item_changed(self, current, previous):
        if current is None:
            return
        self.item_previewed.emit(current.data(Qt.UserRole))

    def _on_scroll_activity(self, value=None):
        if self._update_scroll_badge():
            self.scroll_badge.show()
        self._scroll_hide_timer.stop()
        self._scroll_hide_timer.start()

    def _hide_scrollbar(self):
        if self.scroll_badge.is_dragging():
            # A badge held by the finger must never vanish mid-drag.
            return
        self.scroll_badge.hide()

    def _update_scroll_badge(self):
        """Reposition/resize the floating badge from the scrollbar state.

        Returns True when the list is actually scrollable.
        """
        sb = self.list_widget.verticalScrollBar()
        if sb.maximum() <= 0:
            self.scroll_badge.hide()
            return False
        vp = self.list_widget.viewport()
        track_h = vp.height()
        if track_h <= 0:
            return False
        page = max(1, sb.pageStep())
        total = sb.maximum() + page
        # Minimum grab length comes from the badge (longer in touch mode).
        handle_h = max(self.scroll_badge.min_handle(), int(track_h * page / max(1, total)))
        handle_h = min(handle_h, track_h)
        span = max(1, sb.maximum())
        y = vp.mapTo(self, QPoint(0, 0)).y()
        y += round((sb.value() / span) * (track_h - handle_h))
        self.scroll_badge.reposition(vp, handle_h, y)
        return True

    def eventFilter(self, watched, event):
        if watched is self.list_widget:
            if event.type() == QEvent.Resize:
                # Keep the floating badge glued to the viewport edge.
                self._update_scroll_badge()
            elif event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    current = self.list_widget.currentItem()
                    if current is not None:
                        self.item_double_clicked.emit(current.data(Qt.UserRole))
                        return True
        return super().eventFilter(watched, event)

    def on_item_double_clicked(self, item):
        if item is None:
            return
        self.item_double_clicked.emit(item.data(Qt.UserRole))

    def remove_item_by_url(self, url):
        for i, item in enumerate(self.items_data):
            if (item.get("file_path") or item.get("url")) == url:
                self.items_data.pop(i)
                widget_item = self.list_widget.takeItem(i)
                if widget_item:
                    del widget_item
                # Adjust delegate current index if needed
                delegate = self.list_widget.itemDelegate()
                if hasattr(delegate, "_current_index"):
                    if delegate._current_index == i:
                        delegate._current_index = -1
                    elif delegate._current_index > i:
                        delegate._current_index -= 1
                return

    def soft_remove_item(self, item_data):
        """Remove from visible queue without deleting the underlying file.
        The song will re-appear when the playlist is refreshed."""
        url = item_data.get("file_path") or item_data.get("url")
        self.remove_item_by_url(url)
        self.soft_remove_requested.emit(item_data)

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
            redownload_all_action = menu.addAction("Re-download All in High Quality")
        else:
            redownload_all_action = None
        if data.get("type") == "track" and data.get("file_path"):
            remove_action = menu.addAction("Remove and Delete File")
            redownload_action = menu.addAction("Re-download in High Quality")
        else:
            redownload_action = None
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
        elif action == redownload_action:
            self.redownload_requested.emit(data)
        elif action == redownload_all_action:
            self.redownload_all_requested.emit(data)
