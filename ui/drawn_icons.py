"""Vector-drawn media control icons.

The transport buttons used to rely on unicode glyphs (⏮ ⏭ 🔀 ▶ ⏸ 🔊) and
QStyle standard icons, which render as empty "tofu" boxes on systems whose
font or icon theme does not carry them. Every symbol here is drawn with
QPainter into a QPixmap instead, so the buttons look identical on every
machine no matter which fonts or themes are installed.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap


def _device_pixel_ratio():
    """Device pixel ratio to render icons at (HiDPI screens need > 1)."""
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    return max(1.0, float(screen.devicePixelRatio()))


def _pixmap(size, color):
    """Transparent pixmap plus a painter pre-set to fill with `color`.

    The pixmap is allocated at the screen's device pixel ratio. A plain
    logical-size pixmap gets scaled up by Qt on HiDPI panels, which is why
    the symbols used to look blurred while the button text next to them
    (font-rendered, always resolution aware) stayed crisp.
    """
    dpr = _device_pixel_ratio()
    physical = max(1, int(round(float(size) * dpr)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(physical / float(size))
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
    """Filled triangle at `tip` pointing along `direction` (any length)."""
    tip = QPointF(tip)
    direction = QPointF(direction)
    length = math.hypot(direction.x(), direction.y())
    if length > 0.0:
        direction = QPointF(direction.x() / length, direction.y() / length)
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


def _shuffle_geometry(n):
    """Corner coordinates of the two crossing shuffle arrows at size n.

    Pure coordinate math (no painting) so the layout stays unit-testable:
    the arrowheads must keep clear of each other — the previous layout
    left well under a pixel between them at the 18px button size, so the
    two heads merged into one blob.

    Returns a tuple of (stroke points, head tip, head direction, head size)
    for the up-right and the down-right arrow.
    """
    stub_x0 = 0.08 * n
    corner_x = 0.30 * n
    bend_x = 0.76 * n
    tip_x = 0.84 * n
    head = 0.17 * n
    up = (
        QPointF(stub_x0, 0.70 * n),
        QPointF(corner_x, 0.70 * n),
        QPointF(bend_x, 0.24 * n),
    )
    down = (
        QPointF(stub_x0, 0.30 * n),
        QPointF(corner_x, 0.30 * n),
        QPointF(bend_x, 0.76 * n),
    )
    return (
        (up, QPointF(tip_x, 0.16 * n), QPointF(1.0, -1.0), head),
        (down, QPointF(tip_x, 0.84 * n), QPointF(1.0, 1.0), head),
    )


def shuffle_icon(size, color="#f2f2f2"):
    """Two crossing paths, each ending in an arrowhead.

    The heads point into the top-right and bottom-right corners with
    generous space between them, and _pixmap renders at the screen's
    device pixel ratio so the symbol matches the crispness of the button
    text instead of blurring.
    """
    n = float(size)
    pixmap, painter = _pixmap(size, color)
    painter.setPen(_pen(size, color, max(2.0, 0.08 * n)))

    for points, tip, direction, head in _shuffle_geometry(n):
        path = QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        # One stroked polyline per arrow: the corner between the stub and
        # the diagonal gets a proper round join instead of two separate
        # line caps sitting next to each other.
        painter.strokePath(path, painter.pen())
        _arrowhead(painter, tip, direction, head)
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
