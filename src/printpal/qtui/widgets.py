"""Reusable Qt widgets for the PrintPal UI."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from PIL import Image

from printpal.qtui import theme


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())   # copy so the pixmap owns its bytes


class PreviewCanvas(QWidget):
    """Paints a label image centred on a soft canvas, on white 'paper' with a
    subtle drop shadow -- so a small label doesn't float on a bare rectangle."""

    def __init__(self, pal: theme.Palette):
        super().__init__()
        self._pix: QPixmap | None = None
        self.pal = pal
        self.setMinimumSize(320, 320)

    def set_palette(self, pal: theme.Palette) -> None:
        self.pal = pal
        self.update()

    def set_image(self, img: Image.Image | None) -> None:
        self._pix = pil_to_qpixmap(img) if img is not None else None
        self.update()

    def paintEvent(self, _e) -> None:
        pnt = QPainter(self)
        pnt.setRenderHint(QPainter.Antialiasing)
        pnt.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        pnt.setPen(Qt.NoPen)
        pnt.setBrush(QColor(self.pal.canvas))
        pnt.drawRoundedRect(r, 16, 16)
        if self._pix is None or self._pix.isNull():
            return
        pad = 28
        avail_w = max(1, r.width() - pad * 2)
        avail_h = max(1, r.height() - pad * 2)
        scaled = self._pix.scaled(avail_w, avail_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (r.width() - scaled.width()) // 2
        y = (r.height() - scaled.height()) // 2
        shadow = QColor(0, 0, 0, 60 if not self.pal.dark else 110)
        pnt.setBrush(shadow)
        pnt.drawRoundedRect(x + 3, y + 5, scaled.width(), scaled.height(), 6, 6)
        pnt.setBrush(QColor("#FFFFFF"))
        pnt.drawRoundedRect(x - 1, y - 1, scaled.width() + 2, scaled.height() + 2, 6, 6)
        pnt.drawPixmap(x, y, scaled)


class ThumbTile(QFrame):
    """A clickable thumbnail in the multi-label rail."""

    clicked = Signal(int)

    def __init__(self, index: int, pixmap: QPixmap, caption: str):
        super().__init__()
        self.setObjectName("ThumbTile")
        self.index = index
        self.setProperty("selected", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        img = QLabel()
        img.setPixmap(pixmap)
        img.setAlignment(Qt.AlignCenter)
        cap = QLabel(caption)
        cap.setObjectName("Tiny")
        cap.setAlignment(Qt.AlignCenter)
        lay.addWidget(img)
        lay.addWidget(cap)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, _e) -> None:
        self.clicked.emit(self.index)


def make_chip(text: str, fg: str, bg: str) -> QLabel:
    chip = QLabel(text)
    chip.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:9px; padding:3px 10px; "
        f"font-size:12px; font-weight:700;")
    chip.setAlignment(Qt.AlignCenter)
    return chip


def hline(color: str) -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{color};")
    return line


def row(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    return w
