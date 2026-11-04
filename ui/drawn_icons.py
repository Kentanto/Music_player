"""Vector-drawn media control icons.

The transport buttons used to rely on unicode glyphs (⏮ ⏭ 🔀 ▶ ⏸ 🔊) and
QStyle standard icons, which render as empty "tofu" boxes on systems whose
font or icon theme does not carry them. Every symbol here is drawn with
QPainter into a QPixmap instead, so the buttons look identical on every
machine no matter which fonts or themes are installed.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


def _pixmap(size, color):
    """Transparent pixmap plus a painter pre-set to fill with `color`."""
    pixmap = QPixmap(int(size), int(size))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    return pixmap, painter


def _pen(size, color, width):
    pen = QPen(QColor(color), float(width))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _arrowhead(painter, tip, direction, size):
    """Filled triangle at `tip` pointing along the unit vector `direction`."""
    tip = QPointF(tip)
    direction = QPointF(direction)
    perpendicular = QPointF(-direction.y(), direction.x())
    base_center = tip - direction * size
    a = base_center + perpendicular * size * 0.55
    b = base_center - perpendicular * size * 0.55
    path = QPainterPath()
    path.moveTo(tip)
    path.lineTo(a)
    path.lineTo(b)
    path.closeSubpath()
    painter.drawPath(path)


def play_icon(size, color="#f2f2f2"):
    """Solid right-pointing triangle."""
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    path = QPainterPath()
    path.moveTo(0.30 * n, 0.22 * n)
    path.lineTo(0.30 * n, 0.78 * n)
    path.lineTo(0.78 * n, 0.50 * n)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return pixmap


def pause_icon(size, color="#f2f2f2"):
    """Two vertical bars."""
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    painter.drawRoundedRect(QRectF(0.30 * n, 0.24 * n, 0.13 * n, 0.52 * n), 0.03 * n, 0.03 * n)
    painter.drawRoundedRect(QRectF(0.57 * n, 0.24 * n, 0.13 * n, 0.52 * n), 0.03 * n, 0.03 * n)
    painter.end()
    return pixmap


def prev_icon(size, color="#f2f2f2"):
    """Skip-backward: a bar with a left-pointing triangle beside it."""
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    painter.drawRoundedRect(QRectF(0.22 * n, 0.24 * n, 0.12 * n, 0.52 * n), 0.03 * n, 0.03 * n)
    path = QPainterPath()
    path.moveTo(0.76 * n, 0.24 * n)
    path.lineTo(0.76 * n, 0.76 * n)
    path.lineTo(0.38 * n, 0.50 * n)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return pixmap


def next_icon(size, color="#f2f2f2"):
    """Skip-forward: a right-pointing triangle with a bar beside it."""
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    path = QPainterPath()
    path.moveTo(0.24 * n, 0.24 * n)
    path.lineTo(0.24 * n, 0.76 * n)
    path.lineTo(0.62 * n, 0.50 * n)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawRoundedRect(QRectF(0.66 * n, 0.24 * n, 0.12 * n, 0.52 * n), 0.03 * n, 0.03 * n)
    painter.end()
    return pixmap


def shuffle_icon(size, color="#f2f2f2"):
    """Two crossing paths, each ending in an arrowhead."""
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    painter.setPen(_pen(size, color, max(2.0, 0.08 * n)))
    head = 0.20 * n

    # Bottom-left path that crosses up to the top-right corner.
    painter.drawLine(QPointF(0.10 * n, 0.70 * n), QPointF(0.40 * n, 0.70 * n))
    painter.drawLine(QPointF(0.40 * n, 0.70 * n), QPointF(0.78 * n, 0.32 * n))
    dx, dy = 0.78 - 0.40, 0.32 - 0.70
    length = math.hypot(dx, dy)
    _arrowhead(
        painter,
        QPointF(0.84 * n, 0.26 * n),
        QPointF(dx / length, dy / length),
        head,
    )

    # Top-left path that crosses down to the bottom-right corner.
    painter.drawLine(QPointF(0.10 * n, 0.30 * n), QPointF(0.40 * n, 0.30 * n))
    painter.drawLine(QPointF(0.40 * n, 0.30 * n), QPointF(0.78 * n, 0.68 * n))
    dx, dy = 0.78 - 0.40, 0.68 - 0.30
    length = math.hypot(dx, dy)
    _arrowhead(
        painter,
        QPointF(0.84 * n, 0.74 * n),
        QPointF(dx / length, dy / length),
        head,
    )
    painter.end()
    return pixmap


def volume_icon(size, color="#f2f2f2"):
    """Speaker with two sound waves."""
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    body = QPainterPath()
    body.moveTo(0.16 * n, 0.40 * n)
    body.lineTo(0.30 * n, 0.40 * n)
    body.lineTo(0.46 * n, 0.24 * n)
    body.lineTo(0.46 * n, 0.76 * n)
    body.lineTo(0.30 * n, 0.60 * n)
    body.lineTo(0.16 * n, 0.60 * n)
    body.closeSubpath()
    painter.drawPath(body)

    painter.setPen(_pen(size, color, max(2.0, 0.07 * n)))
    wave = QPainterPath()
    wave.moveTo(0.54 * n, 0.38 * n)
    wave.quadTo(0.66 * n, 0.50 * n, 0.54 * n, 0.62 * n)
    painter.strokePath(wave, painter.pen())
    wave = QPainterPath()
    wave.moveTo(0.54 * n, 0.28 * n)
    wave.quadTo(0.76 * n, 0.50 * n, 0.54 * n, 0.72 * n)
    painter.strokePath(wave, painter.pen())
    painter.end()
    return pixmap
