"""Touch-friendly player bar used when touch-panel mode is enabled."""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSizePolicy
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from .clickable_slider import FocusAwareSlider
from .drawn_icons import next_icon, pause_icon, play_icon, prev_icon, shuffle_icon
from .wedge_volume import WedgeVolumeSlider

class TouchPlayerBar(QWidget):
    play_pause_clicked = Signal()
    next_clicked = Signal()
    prev_clicked = Signal()
    shuffle_toggled = Signal(bool)
    volume_changed = Signal(int)
    seek_requested = Signal(float)
    fullscreen_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playing = False
        self._eq_widget = None
        self.init_ui()

    def init_ui(self):
        main_l = QVBoxLayout()
        main_l.setContentsMargins(10, 6, 10, 6)
        main_l.setSpacing(8)
        # No artificial minimum height: the layout's own minimum keeps the
        # seek bar, buttons and EQ snug, and every spare pixel goes to the
        # cover image above instead of to gaps around here.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)

        # -- Seek bar --
        self.seek_layout = QHBoxLayout()
        self.time_label_start = QLabel("0:00")
        self.time_label_start.setStyleSheet("font-size: 16px; font-weight: bold; color: #c0c0c0;")
        self.time_label_end = QLabel("0:00")
        self.time_label_end.setStyleSheet("font-size: 16px; font-weight: bold; color: #c0c0c0;")
        self.seek_slider = FocusAwareSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("touchSeekSlider")
        self.seek_slider.setRange(0, 100)
        self.seek_slider.setMinimumHeight(48)
        self.seek_slider.setMaximumHeight(56)
        self.seek_slider.sliderMoved.connect(self._on_seek)
        self.seek_slider.focus_changed.connect(self._on_seek_focus)
        self.seek_layout.addWidget(self.time_label_start)
        self.seek_layout.addWidget(self.seek_slider, 1)
        self.seek_layout.addWidget(self.time_label_end)
        main_l.addLayout(self.seek_layout)

        main_l.addStretch(1)

        # -- Middle row: volume (left), buttons (right) --
        middle = QHBoxLayout()
        middle.setSpacing(8)

        # Volume column
        vol_col = QVBoxLayout()
        vol_col.setSpacing(4)
        self.volume_label = QLabel("30")
        self.volume_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        self.volume_label.setAlignment(Qt.AlignCenter)
        vol_col.addWidget(self.volume_label)

        self.wedge_volume = WedgeVolumeSlider(Qt.Orientation.Vertical)
        self.wedge_volume.setObjectName("wedgeVolume")
        self.wedge_volume.valueChanged.connect(lambda v: self.volume_label.setText(str(v)))
        self.wedge_volume.valueChanged.connect(self.volume_changed.emit)
        self.wedge_volume.focus_changed.connect(self._on_volume_focus)
        vol_col.addWidget(self.wedge_volume, 1)
        middle.addLayout(vol_col)

        middle.addStretch(1)

        # Buttons column
        btn_col = QVBoxLayout()
        btn_col.setSpacing(10)

        r1 = QHBoxLayout()
        r1.setSpacing(24)
        r1.addStretch(1)
        self.prev_btn = QPushButton()
        self.prev_btn.setObjectName("touchPrevBtn")
        self.prev_btn.setIconSize(QSize(40, 40))
        self.prev_btn.setIcon(QIcon(prev_icon(40, "#ffffff")))
        self.prev_btn.clicked.connect(self.prev_clicked.emit)
        self.prev_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        r1.addWidget(self.prev_btn)
        self.play_pause_btn = QPushButton()
        self.play_pause_btn.setObjectName("touchPlayBtn")
        self.play_pause_btn.setIconSize(QSize(52, 52))
        self._update_pp_icon()
        self.play_pause_btn.clicked.connect(self.play_pause_clicked.emit)
        self.play_pause_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        r1.addWidget(self.play_pause_btn)
        self.next_btn = QPushButton()
        self.next_btn.setObjectName("touchNextBtn")
        self.next_btn.setIconSize(QSize(40, 40))
        self.next_btn.setIcon(QIcon(next_icon(40, "#ffffff")))
        self.next_btn.clicked.connect(self.next_clicked.emit)
        self.next_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        r1.addWidget(self.next_btn)
        r1.addStretch(1)
        btn_col.addLayout(r1)

        # Stretch between the two rows so the media buttons hug the seek
        # bar above and shuffle/fullscreen hug the EQ below — no dead
        # space at the top or bottom of the cluster.
        btn_col.addStretch(1)

        r2 = QHBoxLayout()
        r2.setSpacing(18)
        r2.addStretch(1)
        self.shuffle_btn = QPushButton("Shuffle")
        self.shuffle_btn.setObjectName("touchShuffleBtn")
        self.shuffle_btn.setIconSize(QSize(28, 28))
        self.shuffle_btn.setIcon(QIcon(shuffle_icon(28, "#ffffff")))
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.toggled.connect(self.shuffle_toggled.emit)
        self.shuffle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        r2.addWidget(self.shuffle_btn)
        self.fullscreen_btn = QPushButton("Fullscreen")
        self.fullscreen_btn.setObjectName("touchFullscreenBtn")
        self.fullscreen_btn.clicked.connect(self.fullscreen_requested.emit)
        self.fullscreen_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        r2.addWidget(self.fullscreen_btn)
        r2.addStretch(1)
        btn_col.addLayout(r2)

        buttons_wrapper = QWidget()
        buttons_wrapper.setLayout(btn_col)
        # Fixed width keeps the cluster hint-sized horizontally; Preferred
        # height lets it fill the wedge's row so both rows hug their
        # neighbours (seek bar above, EQ below).
        buttons_wrapper.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        middle.addWidget(buttons_wrapper)

        middle.addStretch(1)

        # Mirror the volume column's width on the right so the buttons are
        # centered under the cover art (across the full panel width) instead
        # of under the space to the right of the wedge.
        self._btn_balance_spacer = QWidget()
        self._btn_balance_spacer.setFixedWidth(self.wedge_volume.minimumWidth())
        middle.addWidget(self._btn_balance_spacer)

        main_l.addLayout(middle, 3)

        main_l.addStretch(1)

        # -- EQ at bottom --
        self._eq_layout = QHBoxLayout()
        self._eq_layout.setSpacing(10)
        self._eq_placeholder = QWidget()
        self._eq_placeholder.setMinimumHeight(52)
        self._eq_placeholder.setMinimumWidth(80)
        self._eq_placeholder.setMaximumWidth(400)
        self._eq_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._eq_layout.addWidget(self._eq_placeholder, 1)
        main_l.addLayout(self._eq_layout)

        self.setLayout(main_l)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the balancing spacer the same width as the wedge column so
        # the media buttons stay horizontally centered under the cover art.
        wedge_w = self.wedge_volume.width()
        if wedge_w > 0 and self._btn_balance_spacer.width() != wedge_w:
            self._btn_balance_spacer.setFixedWidth(wedge_w)

    def align_seek_row(self, image_x, image_width, panel_width):
        """Align the seek row so it spans exactly from the left edge of the
        cover image to the right edge of the cover image.
        """
        margins = self.layout().contentsMargins()
        left_margin = max(0, image_x - margins.left())
        right_margin = max(0, panel_width - margins.right() - image_x - image_width)
        self.seek_layout.setContentsMargins(left_margin, 0, right_margin, 0)

    def set_eq_widget(self, widget):
        if self._eq_widget is widget:
            return
        if self._eq_widget is not None:
            self._eq_widget.setParent(None)
        self._eq_widget = widget
        if widget is not None:
            self._eq_placeholder.hide()
            self._eq_layout.addWidget(widget, 0)

    def remove_eq_widget(self):
        w = self._eq_widget
        if w is not None:
            self._eq_layout.removeWidget(w)
            w.setParent(None)
            self._eq_widget = None
            self._eq_placeholder.show()
        return w

    def _on_seek(self, value):
        self.seek_requested.emit(value / 100.0)

    def set_seek_position(self, position):
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(int(position * 100))
        self.seek_slider.blockSignals(False)

    def set_time_label(self, current, total):
        self.time_label_start.setText(self._fmt(current))
        self.time_label_end.setText(self._fmt(total))

    @staticmethod
    def _fmt(s):
        if s < 0:
            s = 0
        return f"{int(s)//60}:{int(s)%60:02d}"

    def set_volume(self, value):
        self.wedge_volume.setValue(value)
        self.volume_label.setText(str(value))

    def set_shuffle_state(self, enabled):
        self.shuffle_btn.blockSignals(True)
        self.shuffle_btn.setChecked(enabled)
        self.shuffle_btn.blockSignals(False)

    def set_play_pause_state(self, playing):
        self._playing = bool(playing)
        self._update_pp_icon()

    def _update_pp_icon(self):
        icon = pause_icon(52, "#ffffff") if self._playing else play_icon(52, "#ffffff")
        self.play_pause_btn.setIcon(QIcon(icon))

    def _on_seek_focus(self, focused):
        color = "#1ed760" if focused else "#c0c0c0"
        ss = f"font-size: 16px; font-weight: bold; color: {color};"
        self.time_label_start.setStyleSheet(ss)
        self.time_label_end.setStyleSheet(ss)

    def _on_volume_focus(self, focused):
        color = "#1ed760" if focused else "#ffffff"
        self.volume_label.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {color};")
