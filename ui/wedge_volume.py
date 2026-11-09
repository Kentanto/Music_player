"""Touch-friendly wedge-shaped volume slider.

A right trapezoid (triangle-like) that grows taller from left to right,
mimicking the classic rising-bar volume icon. The entire widget area is
touch-sensitive — no tiny thumb to aim at.
"""

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen


class WedgeVolumeSlider(QWidget):
    """Volume control drawn as a right trapezoid that fills left→right.

    * Bottom edge is perfectly horizontal.
    * Left edge is short (~3 px), right edge is tall (~90 % of height).
    * The top slants from the short left side to the tall right side.
    * Click or drag anywhere inside to set the volume.
    """

    valueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 30
        self.setMinimumHeight(40)
        self.setMaximumHeight(56)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ── public API ──

    def setValue(self, value: int):
        v = max(0, min(100, int(value)))
        if v != self._value:
            self._value = v
            self.update()
            self.valueChanged.emit(v)

    def value(self) -> int:
        return self._value

    # ── mouse interaction ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_value_from_x(int(event.position().x()))
            event.accept()

    def mouseMoveEvent(self, event):
        self._set_value_from_x(int(event.position().x()))
        event.accept()

    def _set_value_from_x(self, x: int):
        w = self.width()
        if w <= 0:
            return
        frac = max(0.0, min(1.0, x / w))
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
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

        # Volume percentage text
        painter.setPen(QColor("#ffffff"))
        painter.drawText(
            self.rect().adjusted(8, 0, -8, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(self._value),
        )

