from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap


class CoverWidget(QWidget):
    """Displays current track: cover art, title, artist"""

    cover_geometry_changed = Signal(int, int)  # cover_label_x, cover_label_width

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cover_pixmap = QPixmap()
        self._fill_pane = True
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        if self._fill_pane:
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            layout.setContentsMargins(10, 10, 10, 10)
        layout.setAlignment(Qt.AlignHCenter)
        layout.setSpacing(4)

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
        self.title_label.setFont(QFont("Arial", 20, QFont.Bold))
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.title_label)

        # Artist
        self.artist_label = QLabel("")
        self.artist_label.setFont(QFont("Arial", 15))
        self.artist_label.setAlignment(Qt.AlignCenter)
        self.artist_label.setWordWrap(True)
        self.artist_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.artist_label)

        layout.addStretch()

        self.setLayout(layout)

    @property
    def cover_label_x(self):
        return self.cover_label.x()

    @property
    def cover_label_w(self):
        return self.cover_label.width()

    def set_fill_pane(self, enabled):
        """Switch between normal margins and full-pane fill mode (panel/touch)."""
        self._fill_pane = bool(enabled)
        layout = self.layout()
        if enabled:
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            layout.setContentsMargins(10, 10, 10, 10)
        self._update_cover_size()
        self._fit_cover_art()
        layout.activate()
        self.update()
        self._emit_geometry()

    def _emit_geometry(self):
        self.cover_geometry_changed.emit(self.cover_label_x, self.cover_label_w)

    def set_track_info(self, title, artist="Unknown Artist"):
        """Update display with track info"""
        self.title_label.setText(title)
        self.artist_label.setText(artist)
        # Text height changed (wrapping), so re-fit the cover art size.
        self._update_cover_size()

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
        self._emit_geometry()

    def _update_cover_size(self):
        layout = self.layout()
        margins = layout.contentsMargins()
        spacing = layout.spacing()
        # Measure the vertical space the text block (title + artist) actually
        # needs at the current width instead of using a fixed reserve, so the
        # cover art takes every remaining pixel below it with no dead gap.
        wrap_w = max(50, self.width() - margins.left() - margins.right())
        text_h = 0
        for lbl in (self.title_label, self.artist_label):
            lh = lbl.heightForWidth(wrap_w)
            if lh <= 0:
                lh = lbl.sizeHint().height()
            text_h += lh
        text_h += spacing * 2 + margins.top() + margins.bottom()

        available_height = max(135, self.height() - text_h)
        if self._fill_pane:
            # Panel mode: full width, and all vertical space the text
            # block doesn't need.
            cover_width = max(240, self.width() - margins.left() - margins.right())
            cover_height = max(135, available_height)
        else:
            # Normal mode: also fill generously
            available_width = max(240, self.width() - 20)
            cover_width = min(available_width, int(available_height * 16 / 9))
            cover_height = max(135, int(cover_width * 9 / 16))
        self.cover_label.setFixedSize(cover_width, cover_height)
        # Force the layout to recalculate the label's position now so
        # _emit_geometry() reads an up-to-date x().
        layout.activate()

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
        self._update_cover_size()
