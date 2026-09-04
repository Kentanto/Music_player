import math
import struct

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtMultimedia import QAudioFormat
from PySide6.QtWidgets import QSizePolicy, QWidget


class EQVisualizer(QWidget):
    """Small animated spectrum display fed by decoded audio buffers."""

    BAND_COUNT = 96
    FFT_SIZE = 2048
    progress_clicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("eqVisualizer")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMinimumWidth(400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(52)
        self._levels = [0.0] * self.BAND_COUNT
        self._targets = [0.0] * self.BAND_COUNT
        self._peak_level = 1.0
        self._progress = 0.0
        self._show_progress = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(33)

    def sizeHint(self):
        return QSize(720, 52)

    def set_show_progress(self, enabled):
        self._show_progress = bool(enabled)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not self._show_progress,
        )
        self.update()

    def set_progress(self, position):
        self._progress = max(0.0, min(1.0, float(position)))
        self.update()

    def mousePressEvent(self, event):
        if self._show_progress and event.button() == Qt.MouseButton.LeftButton:
            self.progress_clicked.emit(self._progress_from_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def _progress_from_x(self, x):
        left = 16
        right = max(left + 1, self.width() - 16)
        return max(0.0, min(1.0, (x - left) / (right - left)))

    @Slot(object)
    def update_buffer(self, buffer):
        audio_format = buffer.format()
        sample_rate = audio_format.sampleRate()
        channels = audio_format.channelCount()
        if sample_rate <= 0 or channels <= 0:
            return

        samples = self._decode_samples(buffer, audio_format.sampleFormat(), channels)
        if len(samples) < 32:
            return

        samples = samples[:self.FFT_SIZE]
        windowed = [
            sample * (0.5 - 0.5 * math.cos(2 * math.pi * index / (len(samples) - 1)))
            for index, sample in enumerate(samples)
        ]
        magnitudes = self._fft_magnitudes(windowed)
        self._targets = self._band_levels(magnitudes, sample_rate)

    def _decode_samples(self, buffer, sample_format, channels):
        raw = buffer.data()
        if not raw:
            return []

        if sample_format == QAudioFormat.SampleFormat.UInt8:
            values = [(value - 128) / 128.0 for value in bytes(raw)]
        elif sample_format == QAudioFormat.SampleFormat.Int16:
            values = [value / 32768.0 for value in struct.unpack("<%dh" % (len(raw) // 2), raw)]
        elif sample_format == QAudioFormat.SampleFormat.Int32:
            values = [value / 2147483648.0 for value in struct.unpack("<%di" % (len(raw) // 4), raw)]
        elif sample_format == QAudioFormat.SampleFormat.Float:
            values = list(struct.unpack("<%df" % (len(raw) // 4), raw))
        else:
            return []

        if channels == 1:
            return values
        return [
            sum(values[index:index + channels]) / channels
            for index in range(0, len(values) - channels + 1, channels)
        ]

    def _fft_magnitudes(self, samples):
        size = 1
        while size < len(samples):
            size <<= 1
        samples = samples + [0.0] * (size - len(samples))
        real = samples[:]
        imaginary = [0.0] * size

        reverse = 0
        for index in range(1, size):
            bit = size >> 1
            while reverse & bit:
                reverse ^= bit
                bit >>= 1
            reverse ^= bit
            if index < reverse:
                real[index], real[reverse] = real[reverse], real[index]

        even = 1
        while even < size:
            angle = -math.pi / even
            sine = math.sin(angle / 2)
            multiplier_real = -2.0 * sine * sine
            multiplier_imaginary = math.sin(angle)
            current_real = 1.0
            current_imaginary = 0.0
            for offset in range(even):
                for index in range(offset, size, even * 2):
                    match = index + even
                    temporary_real = current_real * real[match] - current_imaginary * imaginary[match]
                    temporary_imaginary = current_real * imaginary[match] + current_imaginary * real[match]
                    real[match] = real[index] - temporary_real
                    imaginary[match] = imaginary[index] - temporary_imaginary
                    real[index] += temporary_real
                    imaginary[index] += temporary_imaginary
                next_real = current_real * multiplier_real - current_imaginary * multiplier_imaginary + current_real
                current_imaginary = current_imaginary * multiplier_real + current_real * multiplier_imaginary + current_imaginary
                current_real = next_real
            even <<= 1

        return [math.sqrt(real[index] ** 2 + imaginary[index] ** 2) for index in range(size // 2)]

    def _band_levels(self, magnitudes, sample_rate):
        minimum_frequency = 50.0
        maximum_frequency = min(16000.0, sample_rate / 2)
        levels = []
        for band in range(self.BAND_COUNT):
            low = minimum_frequency * (maximum_frequency / minimum_frequency) ** (band / self.BAND_COUNT)
            high = minimum_frequency * (maximum_frequency / minimum_frequency) ** ((band + 1) / self.BAND_COUNT)
            first_bin = max(1, int(low * len(magnitudes) * 2 / sample_rate))
            last_bin = min(len(magnitudes), max(first_bin + 1, int(high * len(magnitudes) * 2 / sample_rate)))
            average = sum(magnitudes[first_bin:last_bin]) / max(1, last_bin - first_bin)
            levels.append(average)

        frame_peak = max(levels, default=0.0)
        self._peak_level = max(frame_peak, self._peak_level * 0.985, 1.0)
        reference = max(1.0, self._peak_level * 0.62)
        return [
            math.tanh((level / reference) ** 0.82)
            for level in levels
        ]

    def _animate(self):
        self._levels = [
            current + (target - current) * 0.35
            for current, target in zip(self._levels, self._targets)
        ]
        self._targets = [target * 0.96 for target in self._targets]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        left = 12
        right = self.width() - 12
        top = 6
        baseline = self.height() - (23 if self._show_progress else 7)
        available_height = max(1, baseline - top)
        width = max(2, (right - left) / self.BAND_COUNT - 3)

        gradient = QLinearGradient(0, baseline, 0, top)
        gradient.setColorAt(0.0, QColor("#159447"))
        gradient.setColorAt(0.5, QColor("#1db954"))
        gradient.setColorAt(1.0, QColor("#58d878"))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setOpacity(1.0)
        for index, level in enumerate(self._levels):
            x = left + index * (right - left) / self.BAND_COUNT + 1
            height = max(2, int(available_height * level))
            painter.drawRoundedRect(int(x), baseline - height, int(width), height, 1, 1)

        painter.setOpacity(1.0)
        painter.drawLine(left, baseline, right, baseline)


