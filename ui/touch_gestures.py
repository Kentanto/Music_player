"""Touch-screen gesture support (Surface and similar screens).

One application-wide event filter adds three touch-friendly behaviours
without changing how a mouse behaves:

- press and hold about half a second (without dragging) acts as a right
  click, so context menus (songs, playlists) can be opened from a
  touchscreen,
- press, keep held and drag scrolls the queue / playlist list directly;
  slow drags track the finger 1:1, quick flicks scroll further per pixel of
  travel and keep gliding briefly after the finger lifts (ice feel) until
  the speed bleeds off or the screen is touched again,
- inside the fullscreen view: a press while the idle EQ view is showing
  only wakes the controls back up, the bottom controls area is a safe
  zone whose presses never exit fullscreen, and any other tap on empty
  space — touchscreen or mouse — closes the fullscreen view.

Qt synthesizes mouse events from touch input for widgets that do not handle
touch themselves, so this filter only looks at mouse events and works on
both X11 and Wayland.

The press/release bookkeeping is deliberately self-healing. A touch
sequence can lose its release (a popup menu grabbed it, the panel dropped
it), and a handler wedged on such a press would silently swallow every
later one — holds would stop registering until some stray release happened
to free it. So a genuinely new press always takes over the tracking (a
press re-offered to a parent widget — Qt relays mouse events that a child
ignores — is recognised as the same one and left alone), and a hold only
fires while the button is verifiably still down and the row under the
finger is still the row that was pressed.
"""

import time

from PySide6.QtCore import QEvent, QObject, QPersistentModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
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

HOLD_MS = 550         # press-and-hold before it counts as a right click
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

# --- fling momentum (the "ice" glide after a quick flick) ---
# On release the list keeps its scroll speed and bleeds it off smoothly
# instead of hard-stopping. Touching the screen again grabs it at once.
# Every tick steps by the real time that passed since the last one, so
# event-loop jitter never turns the glide into stutter.
FLING_TICK_MS = 16           # reference tick for the friction curve (ms)
FLING_FRICTION = 0.92        # share of the speed kept per reference tick
FLING_MIN_START_PXMS = 0.3    # content speed at release needed to glide
FLING_MAX_SPEED_PXMS = 12.0  # cap so a violent flick stays controllable
FLING_STOP_PXMS = 0.05       # gliding ends under this speed
FLING_HOLD_S = 0.12          # finger parked this long before lifting: no glide
FLING_MAX_DT_MS = 100.0      # after a long stall: one sane catch-up hop only
FLING_GRAB_SWALLOW_PXMS = 0.4  # grabbing a faster glide still swallows the tap

# ── horizontal swipe on queue rows ──
SWIPE_COMMIT_PX = 30     # finger must travel this far before we pick a direction
SWIPE_THRESHOLD_PX = 50  # finger must travel this far to trigger the action


class TouchGestureHandler(QObject):
    """Application-wide gesture filter; owned by the main window."""

    queue_swipe_right = Signal(object)   # item_data
    queue_swipe_left = Signal(object)    # item_data

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window

        self._active = False
        self._press_widget = None
        self._press_index = None
        self._press_global = None
        self._moved = False
        self._hold_fired = False
        self._drag_scrolled = False
        self._scroll_view = None
        self._last_scroll_y = 0.0
        self._last_scroll_time = 0.0
        self._scroll_speed = 0.0      # smoothed finger speed in px/ms (signed)
        self._scroll_residual = 0.0   # sub-unit scroll kept for the next move
        self._fling_stopped_by_press = False
        self._press_in_fullscreen = False
        self._press_in_idle = False
        self._block_clicks_until = 0.0
        # swipe tracking
        self._swipe_dx = 0.0
        self._swipe_dy = 0.0
        self._swipe_horizontal = False
        self._swipe_item_data = None

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(HOLD_MS)
        self._hold_timer.timeout.connect(self._hold_timeout)

        # Momentum after a flick: the list keeps gliding and bleeds off
        # speed until it stops on its own or something is pressed again.
        self._fling_timer = QTimer(self)
        self._fling_timer.setInterval(FLING_TICK_MS)
        self._fling_timer.timeout.connect(self._fling_step)
        self._fling_view = None
        self._fling_speed = 0.0    # content speed of the glide, px/ms
        self._fling_last_time = 0.0  # clock reading of the last glide tick
        self._fling_residual = 0.0

        # The mouse event types this filter cares about. MouseButtonClick
        # (a coalesced click event) only exists on newer Qt; use it if there.
        self._watched_types = {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        }
        self._coalesced_click_type = getattr(QEvent.Type, "MouseButtonClick", None)
        if self._coalesced_click_type is not None:
            self._watched_types.add(self._coalesced_click_type)

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

    def _swiped_item_data(self):
        """Return the UserRole data for the row under the press."""
        if self._press_index is None or not self._press_index.isValid():
            return None
        try:
            item = self._scroll_view.item(self._press_index.row())
            if item is not None:
                return item.data(Qt.UserRole)
        except RuntimeError:
            pass
        return None

    def _blocked_now(self):
        return time.monotonic() < self._block_clicks_until

    def _scroll_gain(self):
        """Scroll-to-finger ratio for the current finger speed.

        1.0 while the finger is slow (exact 1:1 tracking), rising on a
        smooth curve to FLICK_GAIN for fast flicks.
        """
        speed = abs(self._scroll_speed)
        if speed <= SLOW_SPEED_PXMS:
            return 1.0
        if speed >= FLICK_SPEED_PXMS:
            return FLICK_GAIN
        t = (speed - SLOW_SPEED_PXMS) / (FLICK_SPEED_PXMS - SLOW_SPEED_PXMS)
        return 1.0 + (FLICK_GAIN - 1.0) * t * t

    def _scroll_step_px(self, view):
        """Screen pixels one scrollbar unit is worth.

        Views that scroll per pixel count in pixels; per-item views
        count in rows, whose height makes one unit.
        """
        if view.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel:
            return 1.0
        row_height = view.sizeHintForRow(0)
        return float(row_height) if row_height > 0 else 1.0

    @staticmethod
    def _left_button_down():
        """True while the left button is held (real mouse or touch contact).

        Qt keeps this state up to date for the mouse events synthesized
        from touch as well, so it is the reliable way to notice that a
        hold's press is long gone even though its release never arrived.
        """
        return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def _pressed_row_unchanged(self):
        """True if the row under the finger is still the pressed one.

        No list was pressed (or its row could not be resolved): there is
        nothing to compare, the hold may fire.
        """
        if self._scroll_view is None or self._press_index is None:
            return True
        try:
            local = self._press_widget.mapTo(
                self._scroll_view.viewport(), self._press_local.toPoint()
            )
            current = self._scroll_view.indexAt(local)
        except RuntimeError:
            return False  # the pressed widget was destroyed mid-hold
        return (
            self._press_index.isValid()
            and current.isValid()
            and current == self._press_index
        )

    def _start_fling(self, now):
        """Carry flick momentum over the release so the list glides."""
        if not self._drag_scrolled or self._scroll_view is None:
            return
        if now - self._last_scroll_time > FLING_HOLD_S:
            # The finger was parked before lifting: nothing to carry over.
            return
        speed = self._scroll_speed * self._scroll_gain()
        if abs(speed) < FLING_MIN_START_PXMS:
            return  # let go while slow: the list stops with the finger
        if abs(speed) > FLING_MAX_SPEED_PXMS:
            speed = FLING_MAX_SPEED_PXMS if speed > 0.0 else -FLING_MAX_SPEED_PXMS
        self._fling_view = self._scroll_view
        self._fling_speed = speed
        self._fling_residual = 0.0
        self._fling_last_time = now
        self._fling_timer.start()

    def _fling_step(self):
        """One momentum tick: scroll on, then bleed off speed (ice).

        The scroll amount follows the real time since the last tick, so
        late timer ticks (busy main thread) never slow the glide into a
        stutter; the friction curve is scaled by the same elapsed time.
        """
        view = self._fling_view
        if view is None:
            self._stop_fling()
            return
        now = time.monotonic()
        elapsed_ms = (now - self._fling_last_time) * 1000.0
        self._fling_last_time = now
        if elapsed_ms <= 0.0:
            return  # called twice within the clock resolution
        if elapsed_ms > FLING_MAX_DT_MS:
            # The event loop stalled (window drag, heavy paint): catch up
            # one sane hop, do not lurch.
            elapsed_ms = FLING_MAX_DT_MS
        try:
            bar = view.verticalScrollBar()
            step_px = self._scroll_step_px(view)
        except RuntimeError:
            self._stop_fling()  # the view was destroyed mid-glide
            return
        self._fling_residual += self._fling_speed * elapsed_ms / step_px
        whole = int(self._fling_residual)
        self._fling_residual -= whole
        if whole:
            before = bar.value()
            bar.setValue(before - whole)
            if bar.value() == before:
                self._stop_fling()  # ran into the very top or bottom
                return
        self._fling_speed *= FLING_FRICTION ** (elapsed_ms / FLING_TICK_MS)
        if abs(self._fling_speed) < FLING_STOP_PXMS:
            self._stop_fling()

    def _stop_fling(self):
        self._fling_timer.stop()
        self._fling_view = None
        self._fling_speed = 0.0
        self._fling_last_time = 0.0
        self._fling_residual = 0.0

    # ---------- event filter ----------

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type not in self._watched_types:
            return False
        if not isinstance(watched, QWidget):
            return False

        if event_type == QEvent.Type.MouseButtonDblClick:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            # Qt folds a quick second press into a double-click event
            # instead of delivering a fresh press of its own. Track it as
            # the press it really is, so rapid tap-tap and tap-then-swipe
            # sequences keep working. While a hold/drag click-block runs,
            # the event is also kept from the widgets; this same touch's
            # release handles the rest.
            self._on_press(watched, event)
            return self._blocked_now()
        if (
            self._coalesced_click_type is not None
            and event_type == self._coalesced_click_type
        ):
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
        stopped_glide = False
        if self._fling_timer.isActive():
            # Grabbing the gliding list stops it dead. Only a tap that
            # grabbed a still-lively glide counts as a gesture afterwards;
            # when the glide had nearly died, the tap registers normally.
            stopped_glide = abs(self._fling_speed) >= FLING_GRAB_SWALLOW_PXMS
            self._stop_fling()

        if self._active and self._press_global is not None:
            delta = event.globalPosition() - self._press_global
            if abs(delta.x()) < 1.0 and abs(delta.y()) < 1.0:
                # The same physical press re-offered to a parent widget:
                # when a child widget ignores a mouse event, Qt relays it
                # up the parent chain and every relay passes through this
                # filter again. It is not a new finger — the tracking the
                # child dispatch started must stand (otherwise a press on
                # a plain label like the fullscreen artwork would be
                # re-tracked on its parent and lose its context).
                return False

        if self._active:
            # The previous press never saw its release — a popup menu
            # grabbed it, the panel dropped the touch sequence, or a
            # second finger landed elsewhere. Never let that wedge the
            # handler (it would silently swallow every later press, which
            # is how holds randomly stopped registering): the newest press
            # always wins and tracking restarts here, so one lost release
            # costs at most a single gesture instead of all the ones after
            # it.
            self._reset()

        if self._nearest(watched, QMenu) is not None:
            # Presses on popup menus are the menu's own business; arming
            # hold/drag state on them only invites a release the menu eats.
            return False

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
        self._fling_stopped_by_press = stopped_glide
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

        # Snapshot of what was under the finger when it went down. If the
        # list scrolls or gets rebuilt while the finger is held, the hold
        # must cancel instead of opening the menu for whatever song drifted
        # under the same point.
        self._press_index = None
        if self._scroll_view is not None:
            try:
                local = self._press_widget.mapTo(
                    self._scroll_view.viewport(), self._press_local.toPoint()
                )
                index = self._scroll_view.indexAt(local)
                if index.isValid():
                    self._press_index = QPersistentModelIndex(index)
            except RuntimeError:
                pass  # the pressed widget died between the press and here

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
                # Decide direction: horizontal swipe or vertical scroll
                if abs(delta.x()) > abs(delta.y()) + 6:
                    self._swipe_horizontal = True
                    self._swipe_item_data = self._swiped_item_data()
                else:
                    self._drag_scrolled = True
                    self._last_scroll_y = event.globalPosition().y()
                    self._last_scroll_time = time.monotonic()
                    self._scroll_speed = 0.0
                    self._scroll_residual = 0.0

        if self._swipe_horizontal:
            self._swipe_dx = delta.x()
            self._swipe_dy = delta.y()
            return True

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
                    # Signed speed: its sign gives the fling direction.
                    instant = delta_y / elapsed_ms
                    self._scroll_speed += (instant - self._scroll_speed) * 0.4

                # A scrollbar unit is a pixel or a whole row depending on
                # the view's scroll mode; the fraction that did not fill a
                # whole unit is kept and applied on the next move so slow
                # drags are never lost to rounding.
                self._scroll_residual += (
                    delta_y * self._scroll_gain() / self._scroll_step_px(self._scroll_view)
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

        # ── horizontal swipe on queue row ──
        if self._swipe_horizontal and self._swipe_item_data is not None:
            if abs(self._swipe_dx) >= SWIPE_THRESHOLD_PX:
                self._block_clicks_until = time.monotonic() + CLICK_BLOCK_MS / 1000.0
                if self._swipe_dx > 0:
                    self.queue_swipe_right.emit(self._swipe_item_data)
                else:
                    self.queue_swipe_left.emit(self._swipe_item_data)
                self._reset()
                return True

        was_gesture = (
            self._hold_fired or self._drag_scrolled or self._fling_stopped_by_press or self._swipe_horizontal
        )
        if was_gesture:
            self._block_clicks_until = time.monotonic() + CLICK_BLOCK_MS / 1000.0

        # A quick flick keeps its momentum and glides on after the lift.
        self._start_fling(time.monotonic())

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
                elif self._is_interactive(press_widget) or fullscreen.in_click_safe_area(press_widget):
                    # Aimed at a control or inside the always-safe bottom
                    # panel: these presses never exit fullscreen.
                    pass
                else:
                    # A tap on empty space — touchscreen or mouse — exits
                    # fullscreen. Block the leftover click briefly so the
                    # same tap cannot also hit whatever sits underneath in
                    # the main window once the view is gone.
                    self._block_clicks_until = (
                        time.monotonic() + CLICK_BLOCK_MS / 1000.0
                    )
                    fullscreen.close()

        return was_gesture

    def _hold_timeout(self):
        """The finger stayed put long enough — act as a right click."""
        if not self._active or self._hold_fired or self._moved:
            return
        if not self._left_button_down():
            # The release of this press got lost (a popup menu grabbed it,
            # the panel dropped the touch). The finger is no longer on the
            # screen, so firing now would open a context menu at a stale
            # press position — exactly the menu that "opens on a song
            # further down". Reset instead so the next press starts clean.
            self._reset()
            return
        if not self._pressed_row_unchanged():
            # The list scrolled or was rebuilt under the held finger: the
            # point no longer means the song the user is holding, so the
            # menu must not open for whatever row drifted there.
            self._reset()
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
        self._press_index = None
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
        self._fling_stopped_by_press = False
        self._press_in_fullscreen = False
        self._press_in_idle = False
        self._swipe_dx = 0.0
        self._swipe_dy = 0.0
        self._swipe_horizontal = False
        self._swipe_item_data = None
        self._hold_timer.stop()
