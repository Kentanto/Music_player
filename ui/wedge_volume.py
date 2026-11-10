"""Touch-friendly wedge-shaped volume slider.

A right trapezoid (triangle-like) that grows taller from left to right
(horizontal) or wider from bottom to top (vertical, wide edge at the top).
The entire widget area is touch-sensitive — no tiny thumb to aim at.
"""

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen


class WedgeVolumeSlider(QWidget):
    """Volume control drawn as a right trapezoid.

    * Horizontal: bottom edge horizontal, left edge short, right edge tall,
      top slants from short to tall.
    * Vertical (pointing down): left edge straight, top edge wide,
      bottom edge short (the point), right edge slants from wide to short.
      The green fill is anchored at the bottom and grows upward as the
      volume rises.
    * Click or drag anywhere inside to set the volume.
    * Accepts keyboard focus and takes part in remote/keyboard navigation
      like a slider (the main window drives value changes once the wedge
      is highlighted; the outline turns green while highlighted).
    """

    valueChanged = Signal(int)
    focus_changed = Signal(bool)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(parent)
        self._orientation = orientation
        self._value = 30
        if orientation == Qt.Orientation.Horizontal:
            self.setMinimumHeight(40)
            self.setMaximumHeight(56)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            # Vertical: wide at top, point at the bottom, much larger for touch
            self.setMinimumWidth(90)
            self.setMaximumWidth(120)
            self.setMinimumHeight(260)
            self.setMaximumHeight(360)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        # Keyboard/remote navigation gives focus to the highlighted widget;
        # without this the wedge could never receive it.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── public API ──

    def setValue(self, value: int):
        v = max(0, min(100, int(value)))
        if v != self._value:
            self._value = v
            self.update()
            self.valueChanged.emit(v)

    def value(self) -> int:
        return self._value

    def is_remote_highlighted(self) -> bool:
        return bool(self.property("remoteHighlight"))

    # ── keyboard focus ──

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(True)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)
        self.update()

    # ── mouse interaction ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._orientation == Qt.Orientation.Horizontal:
                self._set_value_from_x(int(event.position().x()))
            else:
                self._set_value_from_y(int(event.position().y()))
            event.accept()

    def mouseMoveEvent(self, event):
        if self._orientation == Qt.Orientation.Horizontal:
            self._set_value_from_x(int(event.position().x()))
        else:
            self._set_value_from_y(int(event.position().y()))
        event.accept()

    def _set_value_from_x(self, x: int):
        w = self.width()
        if w <= 0:
            return
        pad = 3
        effective_w = w - pad * 2
        if effective_w <= 0:
            return
        frac = max(0.0, min(1.0, (x - pad) / effective_w))
        self.setValue(int(round(frac * 100)))

    def _set_value_from_y(self, y: int):
        h = self.height()
        if h <= 0:
            return
        pad = 3
        effective_h = h - pad * 2
        if effective_h <= 0:
            return
        frac = max(0.0, min(1.0, 1.0 - (y - pad) / effective_h))
        self.setValue(int(round(frac * 100)))

    # ── painting ──

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        pad = 3

        if self._orientation == Qt.Orientation.Horizontal:
            self._paint_horizontal(painter, w, h, pad)
        else:
            self._paint_vertical(painter, w, h, pad)

    def _outline_pen(self) -> QPen:
        """Grey outline normally, green while highlighted/focused by the
        keyboard or remote navigation (QSS cannot style this custom-drawn
        widget, so the highlight is painted here)."""
        if self.is_remote_highlighted() or self.hasFocus():
            return QPen(QColor("#1ed760"), 2)
        return QPen(QColor(60, 60, 60), 1)

    def _paint_horizontal(self, painter, w, h, pad):
        left_h = max(3, h * 0.08)
        right_h = max(10, h * 0.82)
        by = h - pad

        # Full outline (background)
        outline = QPainterPath()
        outline.moveTo(pad, by)
        outline.lineTo(pad, by - left_h)
        outline.lineTo(w - pad, by - right_h)
        outline.lineTo(w - pad, by)
        outline.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(35, 35, 35))
        painter.drawPath(outline)

        # Filled portion
        fill_w = int((w - pad * 2) * self._value / 100.0)
        if fill_w > 2:
            ratio = fill_w / max(1, w - pad * 2)
            top_y_at_fill = (by - left_h) + (left_h - right_h) * ratio

            fill_path = QPainterPath()
            fill_path.moveTo(pad, by)
            fill_path.lineTo(pad, by - left_h)
            fill_path.lineTo(pad + fill_w, top_y_at_fill)
            fill_path.lineTo(pad + fill_w, by)
            fill_path.closeSubpath()

            grad = QLinearGradient(0, by, 0, pad)
            grad.setColorAt(0.0, QColor("#1aa34a"))
            grad.setColorAt(0.5, QColor("#1db954"))
            grad.setColorAt(1.0, QColor("#58d878"))
            painter.setBrush(grad)
            painter.drawPath(fill_path)

        # Outline stroke
        painter.setPen(self._outline_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

        # Volume percentage text
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            self.rect().adjusted(8, 0, -8, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(self._value),
        )

    def _paint_vertical(self, painter, w, h, pad):
        # Wide top, point at bottom (wedge pointing down, as originally drawn)
        top_w = max(10, w * 0.82)
        bottom_w = max(3, w * 0.08)
        lx = pad
        ty = pad
        by = h - pad

        # Full outline (background)
        outline = QPainterPath()
        outline.moveTo(lx, ty)                    # top-left
        outline.lineTo(lx + top_w, ty)            # top edge (wide)
        outline.lineTo(lx + bottom_w, by)         # right edge slants to bottom point
        outline.lineTo(lx, by)                    # bottom-left
        outline.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(35, 35, 35))
        painter.drawPath(outline)

        # Filled portion (anchored at the bottom, grows upward as volume rises)
        fill_h = int((h - pad * 2) * self._value / 100.0)
        if fill_h > 2:
            ratio = fill_h / max(1, h - pad * 2)
            # As the fill rises, the right edge gets wider
            right_x_at_fill = (lx + bottom_w) + (top_w - bottom_w) * ratio

            fill_path = QPainterPath()
            fill_path.moveTo(lx, by)                        # bottom-left
            fill_path.lineTo(lx + bottom_w, by)             # bottom edge (the point)
            fill_path.lineTo(right_x_at_fill, by - fill_h)  # top-right at fill
            fill_path.lineTo(lx, by - fill_h)                # top-left at fill
            fill_path.closeSubpath()

            grad = QLinearGradient(lx, by, lx, ty)
            grad.setColorAt(0.0, QColor("#1aa34a"))
            grad.setColorAt(0.5, QColor("#1db954"))
            grad.setColorAt(1.0, QColor("#58d878"))
            painter.setBrush(grad)
            painter.drawPath(fill_path)

        # Outline stroke
        painter.setPen(self._outline_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

        # No text drawn here — the number lives in a QLabel above the widget
