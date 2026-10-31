"""Print size & position dialog: fit the page, a percentage of actual size, or
a size in inches -- then anchor the label anywhere on the page and nudge it.

A live preview draws the printer's page with the label laid out by the exact
same arithmetic the printer uses (``printing.place``), so what you see is what
comes out.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)
from PIL import Image

from printpal import printing
from printpal.config import Config
from printpal.printing import MODE_FIT, MODE_PERCENT, MODE_SIZE, PrintLayout
from printpal.qtui import theme
from printpal.qtui.widgets import pil_to_qpixmap

# Used when the printer's page size can't be read (no printer, not Windows).
_DEFAULT_PAGE_IN = (4.0, 6.0)

_ANCHOR_GRID = (("top-left", "top", "top-right"),
                ("left", "center", "right"),
                ("bottom-left", "bottom", "bottom-right"))
_ANCHOR_GLYPH = {"top-left": "↖", "top": "↑", "top-right": "↗",
                 "left": "←", "center": "•", "right": "→",
                 "bottom-left": "↙", "bottom": "↓", "bottom-right": "↘"}


def _placeholder_label() -> Image.Image:
    """A stand-in 4x6 label for previewing when no label is open."""
    from PIL import ImageDraw
    img = Image.new("RGB", (400, 600), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([12, 12, 388, 80], fill="#333333")
    for y in range(110, 330, 34):
        d.rectangle([24, y, 24 + (300 if y % 68 else 220), y + 14], fill="#9A9A9A")
    x = 30
    for i in range(60):
        w = 2 + (i * 7) % 5
        d.rectangle([x, 400, x + w, 560], fill="#222222")
        x += w + 3
        if x > 370:
            break
    return img


class LayoutPreview(QWidget):
    """Paints the page and the label placed on it."""

    def __init__(self, pal: theme.Palette):
        super().__init__()
        self.pal = pal
        self.page_in = _DEFAULT_PAGE_IN
        self.layout_ = PrintLayout()
        self._image: Image.Image | None = None
        self._pix: QPixmap | None = None
        self._pix_rot: QPixmap | None = None
        self.label_dpi = 100.0
        self.setMinimumSize(240, 300)

    def set_label(self, image: Image.Image, dpi: float) -> None:
        # The preview image is small; a thumbnail keeps painting cheap while the
        # physical size comes from the original pixels and DPI.
        self._image = image
        self.label_dpi = dpi
        thumb = image.convert("RGB").copy()
        thumb.thumbnail((700, 700), Image.LANCZOS)
        self._pix = pil_to_qpixmap(thumb)
        self._pix_rot = pil_to_qpixmap(thumb.transpose(Image.ROTATE_90))
        self.update()

    def set_page(self, page_in: tuple[float, float]) -> None:
        self.page_in = page_in
        self.update()

    def set_layout(self, layout: PrintLayout) -> None:
        self.layout_ = layout
        self.update()

    def placement(self) -> printing.Placement | None:
        if self._image is None:
            return None
        pw, ph = self.page_in
        # Work in hundredths of an inch: page units = 100 per inch.
        return printing.place(self._image.width, self._image.height, self.label_dpi,
                              pw * 100, ph * 100, 100, 100, self.layout_)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.pal.canvas))
        p.drawRoundedRect(r, 14, 14)

        pw, ph = self.page_in
        pad = 26
        s = min((r.width() - 2 * pad) / pw, (r.height() - 2 * pad) / ph)
        page = QRectF((r.width() - pw * s) / 2, (r.height() - ph * s) / 2, pw * s, ph * s)
        p.setBrush(QColor(0, 0, 0, 60 if not self.pal.dark else 110))
        p.drawRoundedRect(page.translated(3, 5), 4, 4)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(page, 4, 4)

        pl = self.placement()
        if pl is not None and self._pix is not None:
            k = s / 100.0
            target = QRectF(page.x() + pl.x * k, page.y() + pl.y * k, pl.w * k, pl.h * k)
            p.save()
            p.setClipRect(page)
            p.drawPixmap(target, self._pix_rot if pl.rotated else self._pix,
                         QRectF((self._pix_rot if pl.rotated else self._pix).rect()))
            p.restore()
            # Outline the label; dashed red where it runs off the page.
            off = not page.adjusted(-0.5, -0.5, 0.5, 0.5).contains(target)
            pen = QPen(QColor(self.pal.red if off else theme.BRAND), 1.5,
                       Qt.DashLine if off else Qt.SolidLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(target)

        p.setPen(QColor(self.pal.text_muted))
        p.drawText(QRectF(0, page.bottom() + 4, r.width(), 18), Qt.AlignCenter,
                   f"{pw:.2f} × {ph:.2f} in page")


class LayoutDialog(QDialog):
    """Edits the print size & position in ``config`` (saved on Save)."""

    _page_found = Signal(object)

    def __init__(self, parent, config: Config, printer: str,
                 label_image: Image.Image | None = None, label_dpi: float = 200,
                 dark: bool = False):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Print size & position")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)
        title = QLabel("Print size & position")
        title.setObjectName("H1")
        root.addWidget(title)

        cols = QHBoxLayout()
        cols.setSpacing(24)
        root.addLayout(cols, 1)
        left = QVBoxLayout()
        left.setSpacing(8)
        cols.addLayout(left, 0)

        # -- size --------------------------------------------------------------
        left.addWidget(_section("SIZE"))
        modes = QHBoxLayout()
        modes.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_btns = {}
        for i, (mode, text, tip) in enumerate((
                (MODE_FIT, "Fit page", "Scale the label to fill the printer's page"),
                (MODE_PERCENT, "Percent", "A percentage of the label's actual size"),
                (MODE_SIZE, "Inches", "Fit the label inside a size you choose"))):
            b = QPushButton(text)
            b.setObjectName("NavBtn")
            b.setCheckable(True)
            b.setToolTip(tip)
            self.mode_group.addButton(b, i)
            self.mode_btns[mode] = b
            modes.addWidget(b)
        left.addLayout(modes)

        self.mode_stack = QStackedWidget()
        fit_page = QLabel("The label fills as much of the page as it can, "
                          "keeping its proportions.")
        fit_page.setObjectName("Tiny")
        fit_page.setWordWrap(True)
        self.mode_stack.addWidget(fit_page)

        pct = QWidget()
        pl = QHBoxLayout(pct)
        pl.setContentsMargins(0, 0, 0, 0)
        self.percent = _spin(10, 400, 5, 0, "%")
        self.percent.setValue(config.scale_percent)
        pl.addWidget(self.percent)
        for v in (50, 100, 150):
            q = QPushButton(f"{v}%")
            q.setObjectName("Ghost")
            q.setToolTip("100% = the label's actual size")
            q.clicked.connect(lambda _c, v=v: self.percent.setValue(v))
            pl.addWidget(q)
        pl.addStretch(1)
        self.mode_stack.addWidget(pct)

        size = QWidget()
        sl = QGridLayout(size)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setHorizontalSpacing(8)
        self.width_in = _spin(0.5, 20, 0.25, 2, " in")
        self.width_in.setValue(config.scale_width_in)
        self.height_in = _spin(0.5, 20, 0.25, 2, " in")
        self.height_in.setValue(config.scale_height_in)
        sl.addWidget(QLabel("Width"), 0, 0)
        sl.addWidget(self.width_in, 0, 1)
        sl.addWidget(QLabel("Height"), 1, 0)
        sl.addWidget(self.height_in, 1, 1)
        self.keep = QCheckBox("Keep proportions (fit inside this size)")
        self.keep.setChecked(config.scale_keep_aspect)
        sl.addWidget(self.keep, 2, 0, 1, 2)
        presets = QHBoxLayout()
        for w, h in ((4, 6), (4, 4), (2.25, 4)):
            q = QPushButton(f"{w:g}×{h:g}")
            q.setObjectName("Ghost")
            q.clicked.connect(lambda _c, w=w, h=h: (self.width_in.setValue(w),
                                                     self.height_in.setValue(h)))
            presets.addWidget(q)
        presets.addStretch(1)
        sl.addLayout(presets, 3, 0, 1, 2)
        self.mode_stack.addWidget(size)
        left.addWidget(self.mode_stack)
        # Each mode page only takes the room it needs (no gap under "Fit page").
        self.mode_stack.currentChanged.connect(self._fit_stack)

        # -- position ----------------------------------------------------------
        left.addWidget(_section("POSITION ON THE PAGE"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.anchor_group = QButtonGroup(self)
        self.anchor_btns = {}
        for r, row in enumerate(_ANCHOR_GRID):
            for c, name in enumerate(row):
                b = QPushButton(_ANCHOR_GLYPH[name])
                b.setObjectName("Tool")
                b.setCheckable(True)
                b.setFixedSize(40, 34)
                b.setToolTip(name.replace("-", " ").capitalize())
                self.anchor_group.addButton(b)
                self.anchor_btns[name] = b
                grid.addWidget(b, r, c)
        anchor_row = QHBoxLayout()
        anchor_row.addLayout(grid)
        nudge = QGridLayout()
        nudge.setHorizontalSpacing(8)
        self.off_x = _spin(-10, 10, 0.05, 2, " in")
        self.off_x.setValue(config.offset_x_in)
        self.off_x.setToolTip("Move right (+) or left (−)")
        self.off_y = _spin(-10, 10, 0.05, 2, " in")
        self.off_y.setValue(config.offset_y_in)
        self.off_y.setToolTip("Move down (+) or up (−)")
        nudge.addWidget(QLabel("Nudge →"), 0, 0)
        nudge.addWidget(self.off_x, 0, 1)
        nudge.addWidget(QLabel("Nudge ↓"), 1, 0)
        nudge.addWidget(self.off_y, 1, 1)
        zero = QPushButton("Zero")
        zero.setObjectName("Ghost")
        zero.clicked.connect(lambda: (self.off_x.setValue(0), self.off_y.setValue(0)))
        nudge.addWidget(zero, 2, 1)
        anchor_row.addSpacing(14)
        anchor_row.addLayout(nudge)
        anchor_row.addStretch(1)
        left.addLayout(anchor_row)

        self.readout = QLabel()
        self.readout.setObjectName("Muted")
        self.readout.setWordWrap(True)
        left.addSpacing(4)
        left.addWidget(self.readout)
        self.page_note = QLabel()
        self.page_note.setObjectName("Tiny")
        self.page_note.setWordWrap(True)
        left.addWidget(self.page_note)
        left.addStretch(1)

        # -- preview -----------------------------------------------------------
        self.preview = LayoutPreview(theme.palette(dark))
        self.preview.setMinimumWidth(280)
        if label_image is None:
            self.preview.set_label(_placeholder_label(), 100)
            self._is_placeholder = True
        else:
            self.preview.set_label(label_image, label_dpi)
            self._is_placeholder = False
        cols.addWidget(self.preview, 1)

        btns = QHBoxLayout()
        reset = QPushButton("Reset to Fit")
        reset.setObjectName("Ghost")
        reset.setToolTip("Fill the page, centred -- PrintPal's default")
        reset.clicked.connect(self._reset)
        btns.addWidget(reset)
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

        self._set_mode(config.scale_mode)
        self._fit_stack(self.mode_stack.currentIndex())
        self.anchor_btns.get(config.position, self.anchor_btns["center"]).setChecked(True)
        self.mode_group.idClicked.connect(
            lambda i: self._set_mode((MODE_FIT, MODE_PERCENT, MODE_SIZE)[i]))
        for w in (self.percent, self.width_in, self.height_in, self.off_x, self.off_y):
            w.valueChanged.connect(self._update)
        self.keep.toggled.connect(self._update)
        self.anchor_group.buttonClicked.connect(self._update)

        self.page_note.setText(f"Reading the page size from {printer}…" if printer
                               else "No printer chosen -- previewing a 4 × 6 in page.")
        self._page_found.connect(self._on_page)
        if printer:
            threading.Thread(target=self._find_page, args=(printer,), daemon=True).start()
        self.resize(720, 520)
        self._update()

    # -- state ---------------------------------------------------------------
    def _mode(self) -> str:
        for mode, b in self.mode_btns.items():
            if b.isChecked():
                return mode
        return MODE_FIT

    def _set_mode(self, mode: str) -> None:
        self.mode_btns.get(mode, self.mode_btns[MODE_FIT]).setChecked(True)
        self.mode_stack.setCurrentIndex((MODE_FIT, MODE_PERCENT, MODE_SIZE).index(self._mode()))
        self._update()

    def _fit_stack(self, index: int) -> None:
        for i in range(self.mode_stack.count()):
            pol = self.mode_stack.widget(i).sizePolicy()
            pol.setVerticalPolicy(pol.Policy.Preferred if i == index else pol.Policy.Ignored)
            self.mode_stack.widget(i).setSizePolicy(pol)
        self.mode_stack.adjustSize()

    def _anchor(self) -> str:
        for name, b in self.anchor_btns.items():
            if b.isChecked():
                return name
        return "center"

    def layout_value(self) -> PrintLayout:
        return PrintLayout(mode=self._mode(), percent=self.percent.value(),
                           width_in=self.width_in.value(), height_in=self.height_in.value(),
                           keep_aspect=self.keep.isChecked(), anchor=self._anchor(),
                           offset_x_in=self.off_x.value(), offset_y_in=self.off_y.value())

    def _update(self, *_a) -> None:
        if not hasattr(self, "preview"):
            return
        self.preview.set_layout(self.layout_value())
        pl = self.preview.placement()
        if pl is None:
            return
        pw, ph = self.preview.page_in
        w, h = pl.w / 100, pl.h / 100
        text = f"Prints at {w:.2f} × {h:.2f} in"
        if pl.rotated:
            text += " (turned to match the page)"
        if self._is_placeholder:
            text += " for a 4 × 6 label"
        off = pl.x < -0.5 or pl.y < -0.5 or pl.x + pl.w > pw * 100 + 0.5 or pl.y + pl.h > ph * 100 + 0.5
        if off:
            text += ".  ⚠ Part of it falls off the page and won't print."
        self.readout.setText(text)

    def _find_page(self, printer: str) -> None:
        try:
            size = printing.page_size_inches(printer)
        except Exception:
            size = None
        self._page_found.emit((printer, size))

    def _on_page(self, found) -> None:
        printer, size = found
        if size:
            self.preview.set_page(size)
            self.page_note.setText(f"Page size from {printer}. Change it in the printer's "
                                   "preferences if it's wrong.")
        else:
            self.page_note.setText(f"Couldn't read {printer}'s page size -- previewing "
                                   "a 4 × 6 in page.")
        self._update()

    def _reset(self) -> None:
        self._set_mode(MODE_FIT)
        self.anchor_btns["center"].setChecked(True)
        self.off_x.setValue(0)
        self.off_y.setValue(0)
        self._update()

    def _save(self) -> None:
        lay = self.layout_value()
        c = self.config
        c.scale_mode, c.scale_percent = lay.mode, lay.percent
        c.scale_width_in, c.scale_height_in = lay.width_in, lay.height_in
        c.scale_keep_aspect, c.position = lay.keep_aspect, lay.anchor
        c.offset_x_in, c.offset_y_in = lay.offset_x_in, lay.offset_y_in
        c.clamped()
        try:
            c.save()
        except Exception:
            pass
        self.accept()


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Section")
    return lbl


def _spin(lo: float, hi: float, step: float, decimals: int, suffix: str) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setMinimumWidth(96)
    return s
