from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve


# Visual accent per notification kind, matching the app's palette.
_KIND_STYLES = {
    "success": "#1db954",
    "error": "#e57373",
    "warning": "#ffb74d",
    "info": "#64b5f6",
}

_CARD_QSS = """
QWidget#toastCard {{
    background-color: #1e1e1e;
    border: 1px solid {border};
    border-left: 4px solid {accent};
    border-radius: 6px;
}}
QLabel#toastTitle {{
    background: transparent;
    color: {accent};
    font-size: 13px;
    font-weight: bold;
}}
QLabel#toastMessage {{
    background: transparent;
    color: #ffffff;
    font-size: 12px;
}}
"""


class Toast(QWidget):
    """A single notification card. Lives as a child of the main window,
    stacked from the top. Auto-dismisses, click to dismiss early."""

    MIN_WIDTH = 280
    dismissed = None  # callback(toast) set by ToastManager when used through it

    def __init__(self, title, message="", kind="info", duration_ms=5000,
                 max_width=440, parent=None):
        super().__init__(parent)
        self._closing = False
        accent = _KIND_STYLES.get(kind, _KIND_STYLES["info"])
        border = "#404040" if kind == "info" else accent

        self.setObjectName("toastCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_CARD_QSS.format(accent=accent, border=border))
        self.setWindowFlags(Qt.Widget)  # plain child widget, no own window
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setObjectName("toastTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        if message:
            message_label = QLabel(message)
            message_label.setObjectName("toastMessage")
            message_label.setWordWrap(True)
            layout.addWidget(message_label)

        self._max_width = max_width
        self._adjust_size()

        # Fade in / out (one shared animation; the finished handler checks
        # whether a close was requested).
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(150)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutQuad)
        self._fade.finished.connect(self._on_fade_finished)
        self._fade.start()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(duration_ms)

    def _adjust_size(self):
        """Fit the card to its content, capped at max_width."""
        self.setMinimumWidth(self.MIN_WIDTH)
        self.adjustSize()
        if self.width() > self._max_width:
            self.setFixedWidth(self._max_width)
            self.adjustSize()
            self.setFixedWidth(self._max_width)

    def mousePressEvent(self, event):
        self.dismiss()
        super().mousePressEvent(event)

    def dismiss(self):
        """Fade out and remove the card."""
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._fade.stop()
        self._fade.setDuration(180)
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self):
        if self._closing:
            self._close()

    def _close(self):
        if self.dismissed:
            self.dismissed(self)
        self.hide()
        self.deleteLater()


class ToastManager(QWidget):
    """Owns and positions the active notification cards. The cards are
    child widgets of the host window, stacked downward from the top,
    centered horizontally; nothing below them is blocked (each card is a
    small independent widget, not a full-width overlay)."""

    MARGIN_TOP = 10
    SPACING = 6

    def __init__(self, host):
        super().__init__(host)
        self._host = host
        self._toasts = []
        # Watch the host so cards stay centered when the window resizes.
        host.installEventFilter(self)

    def show_notification(self, title, message="", kind="info", duration_ms=5000):
        toast = Toast(title, message, kind, duration_ms, parent=self._host)
        toast.dismissed = self._on_toast_dismissed
        self._toasts.append(toast)
        self._reposition()
        toast.show()
        toast.raise_()

    def _on_toast_dismissed(self, toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._reposition()

    def _reposition(self):
        available = max(self._host.width() - 20, 120)
        y = self.MARGIN_TOP
        for toast in self._toasts:
            if toast.width() > available:
                toast.setFixedWidth(available)
            x = (self._host.width() - toast.width()) // 2
            toast.move(max(x, 0), y)
            y += toast.height() + self.SPACING

    def eventFilter(self, watched, event):
        if watched is self._host and event.type() == event.Type.Resize:
            self._reposition()
        return super().eventFilter(watched, event)
