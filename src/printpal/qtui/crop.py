"""Adjust-crop dialog: pick a different detected area, or drag your own.

Shows the whole page with every plausible area outlined. Hovering highlights the
area under the pointer (the smallest one, so nested blocks stay reachable);
clicking picks it; dragging draws a custom box. All boxes are in page pixels at
the detection DPI, the same space as ``LabelResult.box``.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from PIL import Image

from printpal.qtui import theme
from printpal.qtui.widgets import pil_to_qpixmap

Box = tuple[int, int, int, int]

_DRAG_PX = 6   # pointer travel before a press becomes a drag


class _CropCanvas(QWidget):
    def __init__(self, page: Image.Image, candidates: list[Box], current: Box,
                 pal: theme.Palette, on_change):
        super().__init__()
        self.pal = pal
        self.page_w, self.page_h = page.size
        self.pix = pil_to_qpixmap(page)
        self.candidates = list(candidates)
        self.selected: Box = tuple(current)
        self.custom = False
        self.hover: Box | None = None
        self._press: QPoint | None = None
        self._drag: Box | None = None
        self._on_change = on_change
        self._scaled = None
        self.setMouseTracking(True)
        self.setMinimumSize(360, 360)
        self.setCursor(Qt.CrossCursor)

    # -- geometry -------------------------------------------------------------
    def _frame(self) -> tuple[float, float, float]:
        """(scale, x offset, y offset) of the page inside the widget."""
        pad = 10
        s = min((self.width() - 2 * pad) / self.page_w, (self.height() - 2 * pad) / self.page_h)
        s = max(s, 0.01)
        ox = (self.width() - self.page_w * s) / 2
        oy = (self.height() - self.page_h * s) / 2
        return s, ox, oy

    def _to_page(self, p: QPoint) -> tuple[float, float]:
        s, ox, oy = self._frame()
        return (p.x() - ox) / s, (p.y() - oy) / s

    def _to_widget(self, b: Box) -> QRectF:
        s, ox, oy = self._frame()
        return QRectF(ox + b[0] * s, oy + b[1] * s, (b[2] - b[0]) * s, (b[3] - b[1]) * s)

    def _hit(self, p: QPoint) -> Box | None:
        x, y = self._to_page(p)
        inside = [c for c in self.candidates if c[0] <= x < c[2] and c[1] <= y < c[3]]
        if not inside:
            return None
        return min(inside, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))

    # -- events ---------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        p = e.position().toPoint()
        if self._press is not None and (p - self._press).manhattanLength() > _DRAG_PX:
            x0, y0 = self._to_page(self._press)
            x1, y1 = self._to_page(p)
            clamp = lambda v, hi: int(max(0, min(hi, v)))  # noqa: E731
            self._drag = (clamp(min(x0, x1), self.page_w), clamp(min(y0, y1), self.page_h),
                          clamp(max(x0, x1), self.page_w), clamp(max(y0, y1), self.page_h))
            self.hover = None
        else:
            self.hover = self._hit(p)
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self._drag = None

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press is None:
            return
        if self._drag is not None:
            b = self._drag
            if b[2] - b[0] > 8 and b[3] - b[1] > 8:
                self.select(b, custom=True)
        else:
            hit = self._hit(e.position().toPoint())
            if hit is not None:
                self.select(hit, custom=False)
        self._press = None
        self._drag = None
        self.update()

    def leaveEvent(self, _e) -> None:
        self.hover = None
        self.update()

    def select(self, box: Box, custom: bool) -> None:
        self.selected = tuple(box)
        self.custom = custom
        self._on_change()
        self.update()

    # -- painting -------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        pnt = QPainter(self)
        pnt.setRenderHint(QPainter.Antialiasing)
        pnt.fillRect(self.rect(), QColor(self.pal.canvas))
        s, ox, oy = self._frame()
        target = QRect(int(ox), int(oy), int(self.page_w * s), int(self.page_h * s))
        if self._scaled is None or self._scaled.size() != target.size():
            self._scaled = self.pix.scaled(target.size(), Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation)
        pnt.drawPixmap(target.topLeft(), self._scaled)
        # Dim everything outside the selection so the chosen crop stands out.
        dim = QColor(0, 0, 0, 70)
        sel = self._to_widget(self._drag or self.selected)
        for r in (QRectF(target.left(), target.top(), target.width(), sel.top() - target.top()),
                  QRectF(target.left(), sel.bottom(), target.width(), target.bottom() - sel.bottom()),
                  QRectF(target.left(), sel.top(), sel.left() - target.left(), sel.height()),
                  QRectF(sel.right(), sel.top(), target.right() - sel.right(), sel.height())):
            if r.width() > 0 and r.height() > 0:
                pnt.fillRect(r, dim)

        brand = QColor(theme.BRAND)
        pen = QPen(QColor(120, 120, 120, 200), 1, Qt.DashLine)
        pnt.setPen(pen)
        pnt.setBrush(Qt.NoBrush)
        for c in self.candidates:
            pnt.drawRect(self._to_widget(c))
        if self.hover is not None:
            fill = QColor(brand)
            fill.setAlpha(45)
            pnt.setBrush(fill)
            pnt.setPen(QPen(brand, 2, Qt.DashLine))
            pnt.drawRect(self._to_widget(self.hover))
        pnt.setBrush(Qt.NoBrush)
        pnt.setPen(QPen(brand, 3))
        pnt.drawRect(sel)


class CropDialog(QDialog):
    """Returns (box, custom) via ``result_box()`` after ``exec()``. ``custom``
    is True for a hand-drawn box (use it exactly, no extra margin)."""

    def __init__(self, parent, page: Image.Image, candidates: list[Box], current: Box,
                 dark: bool):
        super().__init__(parent)
        self.setWindowTitle("Adjust crop")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._page_box = (0, 0, page.width, page.height)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        title = QLabel("Choose what to print")
        title.setObjectName("H2")
        hint = QLabel("Hover to see the areas PrintPal found, click one to use it, "
                      "or drag to draw your own box.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)
        self.canvas = _CropCanvas(page, candidates, current, theme.palette(dark), self._changed)
        root.addWidget(self.canvas, 1)

        btns = QHBoxLayout()
        whole = QPushButton("Whole page")
        whole.setObjectName("Ghost")
        whole.clicked.connect(lambda: self.canvas.select(self._page_box, custom=True))
        reset = QPushButton("Reset")
        reset.setObjectName("Ghost")
        reset.setToolTip("Back to PrintPal's choice")
        reset.clicked.connect(lambda: self.canvas.select(tuple(current), custom=True))
        self.size_lbl = QLabel()
        self.size_lbl.setObjectName("Tiny")
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        use = QPushButton("Use this area")
        use.setObjectName("Primary")
        use.setDefault(True)
        use.clicked.connect(self.accept)
        for w in (whole, reset, self.size_lbl):
            btns.addWidget(w)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(use)
        root.addLayout(btns)

        # Fit the page comfortably on screen, portrait or landscape.
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        h = int(avail.height() * 0.85)
        w = int(min(avail.width() * 0.9, (h - 140) * page.width / page.height + 60))
        self.resize(max(520, w), h)
        self._dpi = None
        self._changed()

    def set_dpi(self, dpi: int) -> None:
        self._dpi = dpi
        self._changed()

    def _changed(self) -> None:
        b = self.canvas.selected
        if self._dpi:
            self.size_lbl.setText(f"{(b[2] - b[0]) / self._dpi:.1f}″ × "
                                  f"{(b[3] - b[1]) / self._dpi:.1f}″")

    def result_box(self) -> tuple[Box, bool]:
        return self.canvas.selected, self.canvas.custom
