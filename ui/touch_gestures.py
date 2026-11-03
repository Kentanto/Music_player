"""Touch-screen gesture support (Surface and similar screens).

One application-wide event filter adds three touch-friendly behaviours
without changing how a mouse behaves:

- press and hold about a second (without dragging) acts as a right click,
  so context menus (songs, playlists) can be opened from a touchscreen,
- press, keep held and drag scrolls the queue / playlist list directly;
  slow drags track the finger 1:1 while quick flicks scroll further per
  pixel of finger travel,
- inside the fullscreen view: a tap while the idle EQ view is showing only
  wakes the controls back up; once the controls are visible, a tap that is
  not on a button or slider exits fullscreen.

Qt synthesizes mouse events from touch input for widgets that do not handle
touch themselves, so this filter only looks at mouse events and works on
both X11 and Wayland.
"""

import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QComboBox,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollBar,
    QTextEdit,
    QWidget,
)

HOLD_MS = 1000        # press-and-hold before it counts as a right click
DRAG_START_PX = 18    # movement before it counts as a drag
CLICK_BLOCK_MS = 400  # how long clicks stay blocked after a hold or drag

# --- velocity-adaptive drag scrolling ---
# A slow drag scrolls 1:1 with the finger. The faster the finger moves,
# the further one pixel of travel scrolls, up to FLICK_GAIN for quick
# flicks. The gain ramps in smoothly between the two speeds so the
# transition never feels like a jump.
SLOW_SPEED_PXMS = 0.35  # finger speed (px/ms) up to which scrolling is 1:1
FLICK_SPEED_PXMS = 3.0  # finger speed at which the full flick gain applies
FLICK_GAIN = 3.5        # scroll distance per finger pixel on fast flicks


class TouchGestureHandler(QObject):
    """Application-wide gesture filter; owned by the main window."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window

        self._active = False
        self._press_widget = None
        self._press_global = None
        self._moved = False
        self._hold_fired = False
        self._drag_scrolled = False
        self._scroll_view = None
        self._last_scroll_y = 0.0
        self._last_scroll_time = 0.0
        self._scroll_speed = 0.0      # smoothed finger speed in px/ms
        self._scroll_residual = 0.0   # sub-unit scroll kept for the next move
        self._press_in_fullscreen = False
        self._press_in_idle = False
        self._block_clicks_until = 0.0

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(HOLD_MS)
        self._hold_timer.timeout.connect(self._hold_timeout)

        # The mouse event types this filter cares about. MouseButtonClick
        # (a coalesced click event) only exists on newer Qt; use it if there.
        self._watched_types = {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        }
        self._click_types = {QEvent.Type.MouseButtonDblClick}
        click_type = getattr(QEvent.Type, "MouseButtonClick", None)
        if click_type is not None:
            self._watched_types.add(click_type)
            self._click_types.add(click_type)

    # ---------- helpers ----------

    def _nearest(self, widget, kind):
        """The closest widget of the given kind at or above `widget`."""
        while widget is not None:
            if isinstance(widget, kind):
                return widget
            widget = widget.parentWidget()
        return None

    def _context_menu_target(self, widget):
        """The closest widget with a custom context menu at or above."""
        while widget is not None:
            if (
                widget.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
                and hasattr(widget, "customContextMenuRequested")
            ):
                return widget
            widget = widget.parentWidget()
        return None

    @staticmethod
    def _is_interactive(widget):
        """Widgets whose taps must keep working instead of exiting fullscreen."""
        return isinstance(
            widget,
            (
                QAbstractButton,
                QAbstractSlider,
                QAbstractItemView,
                QLineEdit,
                QTextEdit,
                QPlainTextEdit,
                QComboBox,
                QMenu,
                QScrollBar,
            ),
        )

    def _blocked_now(self):
        return time.monotonic() < self._block_clicks_until

    def _scroll_gain(self):
        """Scroll-to-finger ratio for the current finger speed.

        1.0 while the finger is slow (exact 1:1 tracking), rising on a
        smooth curve to FLICK_GAIN for fast flicks.
        """
        speed = self._scroll_speed
        if speed <= SLOW_SPEED_PXMS:
            return 1.0
        if speed >= FLICK_SPEED_PXMS:
            return FLICK_GAIN
        t = (speed - SLOW_SPEED_PXMS) / (FLICK_SPEED_PXMS - SLOW_SPEED_PXMS)
        return 1.0 + (FLICK_GAIN - 1.0) * t * t

    def _scroll_step_px(self):
        """Screen pixels one scrollbar unit is worth.

        Views that scroll per pixel count in pixels; per-item views
        count in rows, whose height makes one unit.
        """
        if self._scroll_view.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel:
            return 1.0
        row_height = self._scroll_view.sizeHintForRow(0)
        return float(row_height) if row_height > 0 else 1.0

    # ---------- event filter ----------

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type not in self._watched_types:
            return False
        if not isinstance(watched, QWidget):
            return False

        if event_type in self._click_types:
            # Swallow the leftover click of a hold/drag so a gesture never
            # also activates whatever was under the finger.
            return self._blocked_now()

        button = event.button()
        if event_type == QEvent.Type.MouseMove:
            # Moves report button() == NoButton; the pressed buttons are in
            # buttons() instead.
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                return False
        elif button != Qt.MouseButton.LeftButton:
            return False

        if event_type == QEvent.Type.MouseButtonPress:
            return self._on_press(watched, event)
        if event_type == QEvent.Type.MouseMove:
            return self._on_move(watched, event)
        return self._on_release(watched, event)


    # ---------- gesture state machine ----------

    def _on_press(self, watched, event):
        if self._active:
            return False  # a second finger: ignore

        fullscreen = getattr(self._window, "fullscreen_player", None)
        in_fullscreen = bool(
            fullscreen is not None
            and fullscreen.isVisible()
            and (watched is fullscreen or fullscreen.isAncestorOf(watched))
        )

        self._active = True
        self._press_widget = watched
        self._press_local = event.position()
        self._press_global = event.globalPosition()
        self._moved = False
        self._hold_fired = False
        self._drag_scrolled = False
        self._press_in_fullscreen = in_fullscreen
        # The fullscreen view's own filter wakes the controls on this same
        # press, so _idle_mode may already be False by now. When that press
        # left idle mode it is flagged: treat exactly that press as the
        # wake-up tap (no exit), then consume the flag.
        woke_by_press = bool(getattr(fullscreen, "_last_wake_from_press", False))
        if woke_by_press and in_fullscreen:
            fullscreen._last_wake_from_press = False
        idle_now = bool(getattr(fullscreen, "_idle_mode", False)) if in_fullscreen else False
        self._press_in_idle = idle_now or woke_by_press

        # Drag scrolling engages on item views (queue / playlist lists) but
        # never on the scrollbars themselves or while a menu is opening.
        if isinstance(watched, QScrollBar):
            self._scroll_view = None
        else:
            self._scroll_view = self._nearest(watched, QAbstractItemView)

        self._hold_timer.start()
        return False

    def _on_move(self, watched, event):
        if not self._active:
            return False
        if self._hold_fired:
            return False  # the context menu owns the input now

        delta = event.globalPosition() - self._press_global
        if not self._moved:
            if abs(delta.x()) < DRAG_START_PX and abs(delta.y()) < DRAG_START_PX:
                return False
            self._moved = True
            self._hold_timer.stop()  # "hold" only counts when not dragging
            if self._scroll_view is not None:
                self._drag_scrolled = True
                self._last_scroll_y = event.globalPosition().y()
                self._last_scroll_time = time.monotonic()
                self._scroll_speed = 0.0
                self._scroll_residual = 0.0

        if self._drag_scrolled:
            y = event.globalPosition().y()
            now = time.monotonic()
            delta_y = y - self._last_scroll_y
            self._last_scroll_y = y

            bar = self._scroll_view.verticalScrollBar()
            if bar is not None:
                # Smoothed finger speed (px/ms) picks the scroll-to-finger
                # ratio: 1:1 while slow, amplified for quick flicks.
                elapsed_ms = (now - self._last_scroll_time) * 1000.0
                self._last_scroll_time = now
                if elapsed_ms > 1.0 and delta_y != 0.0:
                    instant = abs(delta_y) / elapsed_ms
                    self._scroll_speed += (instant - self._scroll_speed) * 0.4

                # A scrollbar unit is a pixel or a whole row depending on
                # the view's scroll mode; the fraction that did not fill a
                # whole unit is kept and applied on the next move so slow
                # drags are never lost to rounding.
                self._scroll_residual += (
                    delta_y * self._scroll_gain() / self._scroll_step_px()
                )
                whole = int(self._scroll_residual)
                self._scroll_residual -= whole
                if whole:
                    before = bar.value()
                    bar.setValue(before - whole)
                    if bar.value() == before:
                        # Pressing against the very top or bottom: drop the
                        # leftover so reversing direction answers at once.
                        self._scroll_residual = 0.0
            return True
        return False

    def _on_release(self, watched, event):
        if not self._active:
            return False

        was_gesture = self._hold_fired or self._drag_scrolled
        if was_gesture:
            self._block_clicks_until = time.monotonic() + CLICK_BLOCK_MS / 1000.0

        press_widget = self._press_widget
        in_fullscreen = self._press_in_fullscreen
        press_in_idle = self._press_in_idle
        self._reset()

        if not was_gesture and in_fullscreen:
            fullscreen = getattr(self._window, "fullscreen_player", None)
            if fullscreen is not None and fullscreen.isVisible():
                if press_in_idle:
                    # This tap only woke the controls up (the fullscreen
                    # view handles that itself on any press).
                    pass
                elif not self._is_interactive(press_widget):
                    fullscreen.close()

        return was_gesture

    def _hold_timeout(self):
        """The finger stayed put for a second — act as a right click."""
        if not self._active or self._hold_fired or self._moved:
            return
        self._hold_fired = True
        target = self._context_menu_target(self._press_widget)
        if target is not None:
            # Pure widget-to-widget mapping (no global-position round trip,
            # which is not reliable on every platform). mapTo is valid here
            # because the pressed widget is always a descendant of the
            # context-menu target found above it.
            point = self._press_widget.mapTo(target, self._press_local.toPoint())
            target.customContextMenuRequested.emit(point)

    def _reset(self):
        self._active = False
        self._press_widget = None
        self._press_local = None
        self._press_global = None
        self._moved = False
        self._hold_fired = False
        self._drag_scrolled = False
        self._scroll_view = None
        self._last_scroll_y = 0.0
        self._last_scroll_time = 0.0
        self._scroll_speed = 0.0
        self._scroll_residual = 0.0
        self._press_in_fullscreen = False
        self._press_in_idle = False
        self._hold_timer.stop()
