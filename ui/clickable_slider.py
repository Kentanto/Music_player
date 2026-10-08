from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class FocusAwareSlider(QSlider):
    """Slider that jumps to the clicked point and emits focus_changed(bool).

    QSlider's default groove-click behavior depends on the platform style
    (partial page-steps on Windows, near-nothing on Linux), so mouse handling
    is taken over here: a left click anywhere on the slider sets the value to
    the clicked position immediately, and dragging scrubs to the pointer.
    Keyboard/wheel/focus behavior is untouched.
    """

    focus_changed = Signal(bool)

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._scrubbing = False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def _value_at(self, position):
        """Map a point inside the slider to its value, style-independently."""
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        if self.orientation() == Qt.Orientation.Horizontal:
            offset = position.x() - groove.x() - handle.width() / 2
            span = groove.width() - handle.width()
        else:
            offset = groove.y() + groove.height() - handle.height() / 2 - position.y()
            span = groove.height() - handle.height()
        if span <= 0:
            return self.value()
        fraction = max(0.0, min(offset / span, 1.0))
        return round(self.minimum() + (self.maximum() - self.minimum()) * fraction)

    def _scrub_to(self, position):
        value = self._value_at(position)
        self.setValue(value)
        # setValue() alone does not emit sliderMoved, but the seek logic
        # in the app listens to sliderMoved for manual interaction.
        self.sliderMoved.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = True
            self._scrub_to(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._scrubbing:
            self._scrub_to(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._scrubbing and event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing = False
            event.accept()
            return
        super().mouseReleaseEvent(event)