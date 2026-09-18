"""PrintPal main window (PySide6)."""
from __future__ import annotations

import datetime as _dt
import os
import sys

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QComboBox,
    QStackedWidget, QVBoxLayout, QWidget,
)
from PIL import Image

from printpal import ingest, routing
from printpal.batch import DONE, FAILED, PRINTING, PrintQueue, QUEUED, QueueItem
from printpal.carrier import parse_tracking
from printpal.config import Config
from printpal.history import History, HistoryEntry
from printpal.pipeline import ProcessedLabel
from printpal.printing import list_printers, print_label
from printpal.qtui import theme
from printpal.qtui.settings import SettingsDialog
from printpal.qtui.widgets import PreviewCanvas, ThumbTile, make_chip, pil_to_qpixmap, row
from printpal.qtui.workers import BatchWorker, DetectWorker, EnqueueWorker

NAV_LABEL, NAV_QUEUE, NAV_HISTORY = 0, 1, 2

_SUPPORTED = (".pdf", ".xps", ".oxps", ".png", ".jpg", ".jpeg",
              ".tif", ".tiff", ".bmp", ".gif", ".webp")


def _asset_path(name: str) -> str | None:
    roots = []
    if hasattr(sys, "_MEIPASS"):
        roots += [os.path.join(sys._MEIPASS, "assets"), sys._MEIPASS]
    here = os.path.dirname(os.path.abspath(__file__))
    roots += [os.path.join(here, "..", "..", "..", "assets"),
              os.path.join(here, "..", "..", "assets")]
    for r in roots:
        c = os.path.join(r, name)
        if os.path.isfile(c):
            return c
    return None


class MainWindow(QWidget):
    def __init__(self, config: Config, initial_path: str | None = None, log=None):
        super().__init__()
        self.config = config
        self.log = log
        self.dark = config.dark_mode
        self.pool = QThreadPool.globalInstance()
        self.history = History()
        self.queue = PrintQueue(config, print_label, history=self.history)

        self.labels: list[ProcessedLabel] = []
        self.selected = 0
        self._busy = False
        self._tiles: list[ThumbTile] = []
        self._queue_rows: dict[int, QWidget] = {}

        self.setObjectName("Root")
        self.setWindowTitle("PrintPal")
        self.setAcceptDrops(True)
        self.setMinimumSize(940, 660)
        self.resize(1040, 720)
        icon = _asset_path("icon.png")
        if icon:
            self.setWindowIcon(QIcon(icon))

        self._build()
        self.apply_theme()

        QShortcut(QKeySequence("Ctrl+O"), self, self.open_files)
        QShortcut(QKeySequence("Ctrl+P"), self, self.print_current)
        QShortcut(QKeySequence("Ctrl+R"), self, self.reprint_last)
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self._select_delta(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self._select_delta(1))

        self._show_empty()
        self._refresh_history()
        self._update_nav_counts()
        self._start_watchers()

        if initial_path:
            QTimer.singleShot(60, lambda: self.load_path(initial_path))

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_nav())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_label_view())
        self.stack.addWidget(self._build_queue_view())
        self.stack.addWidget(self._build_history_view())
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 12, 20, 12)
        bl.addWidget(self.stack)
        root.addWidget(body, 1)

        root.addWidget(self._build_statusbar())

    def _build_header(self) -> QWidget:
        h = QFrame()
        h.setObjectName("Header")
        lay = QHBoxLayout(h)
        lay.setContentsMargins(20, 12, 16, 12)
        lay.setSpacing(12)

        logo = _asset_path("icon.png")
        if logo:
            lbl = QLabel()
            lbl.setPixmap(QPixmap(logo).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lay.addWidget(lbl)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        wm = QLabel("PrintPal"); wm.setObjectName("Wordmark")
        tg = QLabel("Shipping label cropper & printer"); tg.setObjectName("Tagline")
        titles.addWidget(wm); titles.addWidget(tg)
        lay.addLayout(titles)
        lay.addStretch(1)

        self.btn_open = QPushButton("Open…"); self.btn_open.setObjectName("Ghost")
        self.btn_open.clicked.connect(self.open_files)
        self.btn_paste = QPushButton("Paste"); self.btn_paste.setObjectName("Ghost")
        self.btn_paste.clicked.connect(self.paste_clipboard)
        self.btn_theme = QPushButton("☾"); self.btn_theme.setObjectName("IconBtn")
        self.btn_theme.setToolTip("Toggle light / dark")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.btn_settings = QPushButton("⚙"); self.btn_settings.setObjectName("IconBtn")
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.clicked.connect(self.open_settings)
        for b in (self.btn_open, self.btn_paste, self.btn_theme, self.btn_settings):
            lay.addWidget(b)
        return h

    def _build_nav(self) -> QWidget:
        w = QFrame()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(6)
        self.nav_group = QButtonGroup(self)
        self.nav_btns = []
        for i, text in enumerate(("Label", "Queue", "History")):
            b = QPushButton(text); b.setObjectName("NavBtn"); b.setCheckable(True)
            b.clicked.connect(lambda _c, idx=i: self._go(idx))
            self.nav_group.addButton(b, i)
            self.nav_btns.append(b)
            lay.addWidget(b)
        self.nav_btns[0].setChecked(True)
        lay.addStretch(1)
        return w

    def _go(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.nav_btns[idx].setChecked(True)
        if idx == NAV_HISTORY:
            self._refresh_history()

    def _update_nav_counts(self) -> None:
        q = len(self.queue.items)
        self.nav_btns[NAV_QUEUE].setText(f"Queue ({q})" if q else "Queue")
        n = self.history.count()
        self.nav_btns[NAV_HISTORY].setText(f"History ({n})" if n else "History")

    # ----------------------------------------------------------- label view
    def _build_label_view(self) -> QWidget:
        self.label_view = QStackedWidget()
        self.label_view.addWidget(self._build_empty_card())   # 0 = empty
        self.label_view.addWidget(self._build_results())       # 1 = results
        return self.label_view

    def _build_empty_card(self) -> QWidget:
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.addStretch(1)
        card = QFrame(); card.setObjectName("Card")
        card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(56, 44, 56, 44)
        cl.setSpacing(10)
        cl.setAlignment(Qt.AlignCenter)
        logo = _asset_path("icon.png")
        if logo:
            l = QLabel(); l.setPixmap(QPixmap(logo).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            l.setAlignment(Qt.AlignCenter); cl.addWidget(l)
        h = QLabel("Drop a shipping label here"); h.setObjectName("H1"); h.setAlignment(Qt.AlignCenter)
        sub = QLabel("PrintPal finds the label, crops the clutter, straightens it,\n"
                     "and sends it to your label printer.")
        sub.setObjectName("Muted"); sub.setAlignment(Qt.AlignCenter)
        cl.addWidget(h); cl.addWidget(sub)
        btns = QHBoxLayout(); btns.setAlignment(Qt.AlignCenter); btns.setSpacing(10)
        openb = QPushButton("Open a file…"); openb.setObjectName("Primary"); openb.clicked.connect(self.open_files)
        pasteb = QPushButton("Paste from clipboard"); pasteb.setObjectName("Ghost"); pasteb.clicked.connect(self.paste_clipboard)
        btns.addWidget(openb); btns.addWidget(pasteb)
        cl.addSpacing(8); cl.addLayout(btns)
        tip = QLabel("Tip: install the PrintPal printer and print to it from any app.")
        tip.setObjectName("Tiny"); tip.setAlignment(Qt.AlignCenter)
        cl.addSpacing(6); cl.addWidget(tip)
        outer.addWidget(card, 0, Qt.AlignCenter)
        outer.addStretch(1)
        return wrap

    def _build_results(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(12)

        top = QHBoxLayout(); top.setSpacing(14)
        # thumbnail rail
        self.rail = QScrollArea(); self.rail.setWidgetResizable(True); self.rail.setFixedWidth(150)
        self.rail_inner = QWidget(); self.rail_lay = QVBoxLayout(self.rail_inner)
        self.rail_lay.setContentsMargins(2, 2, 2, 2); self.rail_lay.setSpacing(8); self.rail_lay.addStretch(1)
        self.rail.setWidget(self.rail_inner)
        top.addWidget(self.rail)

        self.canvas = PreviewCanvas(theme.palette(self.dark))
        top.addWidget(self.canvas, 1)
        lay.addLayout(top, 1)

        # info row
        self.info = QHBoxLayout(); self.info.setSpacing(12)
        info_wrap = QWidget(); info_wrap.setLayout(self.info)
        lay.addWidget(info_wrap)

        # action bar
        bar = QFrame(); bar.setObjectName("Card")
        ab = QHBoxLayout(bar); ab.setContentsMargins(14, 10, 14, 10); ab.setSpacing(8)
        self.btn_ccw = QPushButton("↺"); self.btn_ccw.setObjectName("Tool"); self.btn_ccw.setToolTip("Rotate left")
        self.btn_ccw.clicked.connect(lambda: self._rotate(False))
        self.btn_cw = QPushButton("↻"); self.btn_cw.setObjectName("Tool"); self.btn_cw.setToolTip("Rotate right")
        self.btn_cw.clicked.connect(lambda: self._rotate(True))
        self.btn_save = QPushButton("Save…"); self.btn_save.setObjectName("Tool")
        self.btn_save.clicked.connect(self.save_current)
        ab.addWidget(self.btn_ccw); ab.addWidget(self.btn_cw); ab.addWidget(self.btn_save)
        ab.addStretch(1)
        ab.addWidget(QLabel("Copies"))
        self.copies = QSpinBox(); self.copies.setRange(1, 99); self.copies.setValue(self.config.copies)
        self.copies.setFixedWidth(60); ab.addWidget(self.copies)
        ab.addWidget(QLabel("Printer"))
        self.printer_combo = QComboBox(); self.printer_combo.setMinimumWidth(200)
        self._reload_printers(); self.printer_combo.currentTextChanged.connect(self._on_printer_changed)
        ab.addWidget(self.printer_combo)
        self.btn_printall = QPushButton("Print all"); self.btn_printall.setObjectName("Ghost")
        self.btn_printall.clicked.connect(self.print_all_current)
        ab.addWidget(self.btn_printall)
        self.btn_print = QPushButton("Print  ▸"); self.btn_print.setObjectName("Primary")
        self.btn_print.clicked.connect(self.print_current)
        ab.addWidget(self.btn_print)
        lay.addWidget(bar)
        return w

    # ------------------------------------------------------------- queue view
    def _build_queue_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("Print queue"); t.setObjectName("H1"); head.addWidget(t)
        self.queue_summary = QLabel(""); self.queue_summary.setObjectName("Muted")
        head.addWidget(self.queue_summary); head.addStretch(1)
        self.route_check = None
        add = QPushButton("Add files…"); add.setObjectName("Ghost"); add.clicked.connect(self.open_files)
        clr = QPushButton("Clear"); clr.setObjectName("Ghost"); clr.clicked.connect(self.clear_queue)
        self.btn_printqueue = QPushButton("Print all"); self.btn_printqueue.setObjectName("Primary")
        self.btn_printqueue.clicked.connect(self.print_queue)
        head.addWidget(add); head.addWidget(clr); head.addWidget(self.btn_printqueue)
        lay.addLayout(head)

        from PySide6.QtWidgets import QCheckBox
        self.queue_route = QCheckBox("Smart routing: labels → thermal, documents → paper")
        self.queue_route.setToolTip("Off by default. Set the two printers in Settings.")
        lay.addWidget(self.queue_route)

        self.queue_scroll = QScrollArea(); self.queue_scroll.setWidgetResizable(True)
        self.queue_inner = QWidget(); self.queue_lay = QVBoxLayout(self.queue_inner)
        self.queue_lay.setContentsMargins(0, 0, 0, 0); self.queue_lay.setSpacing(8); self.queue_lay.addStretch(1)
        self.queue_scroll.setWidget(self.queue_inner)
        lay.addWidget(self.queue_scroll, 1)
        self.queue_empty = QLabel("Drop a folder or several files here to batch-print.")
        self.queue_empty.setObjectName("Muted"); self.queue_empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.queue_empty)
        return w

    # ----------------------------------------------------------- history view
    def _build_history_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel("History"); t.setObjectName("H1"); head.addWidget(t); head.addStretch(1)
        last = QPushButton("Reprint last (Ctrl+R)"); last.setObjectName("Ghost"); last.clicked.connect(self.reprint_last)
        clr = QPushButton("Clear"); clr.setObjectName("Ghost"); clr.clicked.connect(self.clear_history)
        head.addWidget(last); head.addWidget(clr)
        lay.addLayout(head)
        self.history_scroll = QScrollArea(); self.history_scroll.setWidgetResizable(True)
        self.history_inner = QWidget(); self.history_lay = QVBoxLayout(self.history_inner)
        self.history_lay.setContentsMargins(0, 0, 0, 0); self.history_lay.setSpacing(8); self.history_lay.addStretch(1)
        self.history_scroll.setWidget(self.history_inner)
        lay.addWidget(self.history_scroll, 1)
        self.history_empty = QLabel("Nothing printed yet. Prints show up here for one-click reprint.")
        self.history_empty.setObjectName("Muted"); self.history_empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.history_empty)
        return w

    def _build_statusbar(self) -> QWidget:
        w = QFrame(); w.setObjectName("Header")
        lay = QHBoxLayout(w); lay.setContentsMargins(20, 6, 20, 6)
        self.status = QLabel("Ready."); self.status.setObjectName("Status")
        lay.addWidget(self.status); lay.addStretch(1)
        self.progress = QProgressBar(); self.progress.setFixedWidth(160); self.progress.setRange(0, 1)
        self.progress.hide()
        lay.addWidget(self.progress)
        return w

    # --------------------------------------------------------------- theming
    def apply_theme(self) -> None:
        self.setStyleSheet(theme.stylesheet(self.dark))
        self.btn_theme.setText("☀" if self.dark else "☾")
        if hasattr(self, "canvas"):
            self.canvas.set_palette(theme.palette(self.dark))
        self._render_selected()

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        self.config.dark_mode = self.dark
        try:
            self.config.save()
        except Exception:
            pass
        self.apply_theme()

    # -------------------------------------------------------------- printers
    def _reload_printers(self) -> None:
        try:
            printers = list_printers()
        except Exception:
            printers = []
        if self.config.printer and self.config.printer not in printers:
            printers = [self.config.printer] + printers
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear(); self.printer_combo.addItems(printers)
        if self.config.printer:
            self.printer_combo.setCurrentText(self.config.printer)
        self.printer_combo.blockSignals(False)

    def _on_printer_changed(self, name: str) -> None:
        if name:
            self.config.printer = name
            try:
                self.config.save()
            except Exception:
                pass

    # ------------------------------------------------------------- input
    def open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose label(s)", "",
            "Label files (*.pdf *.xps *.oxps *.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All files (*.*)")
        if not paths:
            return
        if len(paths) == 1:
            self.load_path(paths[0])
        else:
            self.enqueue(paths)

    def paste_clipboard(self) -> None:
        try:
            from printpal.clipboard import get_pdf_path
            path = get_pdf_path()
        except Exception:
            path = None
        if path:
            self.load_path(path)
        else:
            self.status.setText("Clipboard has no label file. Copy a label, then paste.")

    def load_path(self, path: str) -> None:
        if self._busy:
            return
        err = _validate(path)
        if err:
            self._on_detect_error(err)
            return
        self._busy = True
        self._go(NAV_LABEL)
        self.status.setText(f"Reading {os.path.basename(path)}…")
        self.progress.setRange(0, 0); self.progress.show()
        worker = DetectWorker(path, self.config)
        # Cross-thread signals -> main-thread slots (Qt queues them automatically).
        worker.signals.done.connect(self._on_detect_done)
        worker.signals.error.connect(self._on_detect_error)
        worker.signals.progress.connect(self.status.setText)
        self.pool.start(worker)

    def _on_detect_done(self, labels: list) -> None:
        self._busy = False
        self.progress.hide(); self.progress.setRange(0, 1)
        printable = [l for l in labels if l.is_printable]
        self.labels = printable or labels
        self.selected = 0
        if not self.labels or not self.labels[0].is_printable:
            self.status.setText("No label found in that file.")
            self._show_empty()
            return
        self._show_results()
        n = sum(1 for l in self.labels if l.is_label)
        if n == 0:
            self.status.setText(f"No shipping label detected — showing {len(self.labels)} page(s).")
        else:
            self.status.setText(f"Found {n} label{'s' if n != 1 else ''} · "
                                f"{int(self.labels[0].confidence * 100)}% confident")
        if self.config.auto_print and len(self.labels) == 1 and self.labels[0].is_label \
                and self.labels[0].confidence >= self.config.auto_print_min_confidence:
            QTimer.singleShot(150, self.print_current)

    def _on_detect_error(self, msg: str) -> None:
        self._busy = False
        self.progress.hide(); self.progress.setRange(0, 1)
        self.status.setText("Couldn't read that file.")
        QMessageBox.warning(self, "PrintPal", msg)

    # ------------------------------------------------------------- label view state
    def _show_empty(self) -> None:
        self.label_view.setCurrentIndex(0)

    def _show_results(self) -> None:
        self.label_view.setCurrentIndex(1)
        self.copies.setValue(self.config.copies)
        self._build_rail()
        self.btn_printall.setVisible(len(self.labels) > 1)
        self._render_selected()

    def _build_rail(self) -> None:
        for t in self._tiles:
            t.setParent(None)
        self._tiles = []
        multi = len(self.labels) > 1
        self.rail.setVisible(multi)
        if not multi:
            return
        for i, lab in enumerate(self.labels):
            thumb = lab.preview_image.copy()
            thumb.thumbnail((120, 150), Image.LANCZOS)
            cap = f"Label {i + 1}"
            tile = ThumbTile(i, pil_to_qpixmap(thumb), cap)
            tile.clicked.connect(self._select)
            self.rail_lay.insertWidget(self.rail_lay.count() - 1, tile)
            self._tiles.append(tile)
        self._highlight_rail()

    def _highlight_rail(self) -> None:
        for i, t in enumerate(self._tiles):
            t.set_selected(i == self.selected)

    def _select(self, idx: int) -> None:
        if 0 <= idx < len(self.labels):
            self.selected = idx
            self._render_selected()

    def _select_delta(self, d: int) -> None:
        if self.labels:
            self._select((self.selected + d) % len(self.labels))

    def _render_selected(self) -> None:
        if not self.labels or self.label_view.currentIndex() != 1:
            return
        lab = self.labels[self.selected]
        self.canvas.set_image(lab.preview_image)
        self._highlight_rail()
        self._render_info(lab)

    def _render_info(self, lab: ProcessedLabel) -> None:
        while self.info.count():
            item = self.info.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        r = lab.result
        p = theme.palette(self.dark)
        text, fg, bg = theme.confidence_style(r.confidence, p)
        self.info.addWidget(make_chip(f"{text} · {int(r.confidence * 100)}%", fg, bg))
        w_in = lab.preview_image.width / r.detect_dpi
        h_in = lab.preview_image.height / r.detect_dpi
        self.info.addWidget(self._muted(f"{w_in:.1f}″ × {h_in:.1f}″"))
        ship = parse_tracking(r.barcode_data)
        if ship.tracking:
            self.info.addWidget(self._muted(f"{ship.carrier} · {ship.tracking}"))
        if lab.region_count > 1:
            self.info.addWidget(self._muted(f"Label {lab.region_index + 1} of {lab.region_count}"))
        elif lab.page_count > 1:
            self.info.addWidget(self._muted(f"Page {lab.page_index + 1} of {lab.page_count}"))
        self.info.addStretch(1)
        if r.warnings:
            warn = QLabel("⚠  " + r.warnings[0]); warn.setWordWrap(True)
            warn.setStyleSheet(f"color:{p.amber}; font-size:12px;")
            self.info.addWidget(warn)

    def _muted(self, text: str) -> QLabel:
        l = QLabel(text); l.setObjectName("Muted")
        return l

    def _rotate(self, cw: bool) -> None:
        if not self.labels:
            return
        lab = self.labels[self.selected]
        lab.rotate_cw() if cw else lab.rotate_ccw()
        if self._tiles:
            thumb = lab.preview_image.copy(); thumb.thumbnail((120, 150), Image.LANCZOS)
            self._build_rail()
        self._render_selected()

    # ------------------------------------------------------------- printing (label view)
    def _sync(self) -> None:
        self.config.copies = self.copies.value()
        self.config.printer = self.printer_combo.currentText() or self.config.printer

    def print_current(self) -> None:
        if not self.labels or self._busy:
            return
        self._sync()
        lab = self.labels[self.selected]
        self._print_one(lab)

    def print_all_current(self) -> None:
        if not self.labels:
            return
        self._sync()
        ok = 0
        for lab in self.labels:
            if lab.is_printable and self._print_one(lab, silent=True):
                ok += 1
        self.status.setText(f"Printed {ok} of {len(self.labels)} to {self.config.printer}.")

    def _print_one(self, lab: ProcessedLabel, silent: bool = False) -> bool:
        try:
            image = lab.render_print_image(self.config)
        except Exception as e:  # noqa: BLE001
            if not silent:
                QMessageBox.critical(self, "PrintPal", f"Couldn't render the label.\n\n{e}")
            return False
        try:
            print_label(image, self.config.printer, copies=self.config.copies)
        except Exception as e:  # noqa: BLE001
            if not silent:
                QMessageBox.critical(self, "PrintPal – print error", str(e))
            self.status.setText("Print failed.")
            return False
        self._log_history(lab, image)
        if not silent:
            self.status.setText(f"Sent to {self.config.printer}"
                                + (f" × {self.config.copies}" if self.config.copies > 1 else "") + ".")
        try:
            self.config.save()
        except Exception:
            pass
        self._refresh_history(); self._update_nav_counts()
        return True

    def _log_history(self, lab: ProcessedLabel, image: Image.Image) -> None:
        try:
            ship = parse_tracking(lab.result.barcode_data)
            entry = HistoryEntry(
                created_at=0.0, source_name=os.path.basename(lab.source_path),
                carrier=ship.carrier, tracking=ship.tracking, kind=lab.kind,
                copies=self.config.copies, printer=self.config.printer,
                page_index=lab.page_index, region_index=lab.region_index,
                dpi=self.config.print_dpi)
            self.history.record(entry, image)
        except Exception:
            pass

    def save_current(self) -> None:
        if not self.labels:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save cropped label", "label.png",
                                              "PNG image (*.png)")
        if not path:
            return
        try:
            self.labels[self.selected].render_print_image(self.config).save(path)
            self.status.setText(f"Saved {os.path.basename(path)}.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "PrintPal", f"Couldn't save.\n\n{e}")

    # ------------------------------------------------------------- queue
    def enqueue(self, paths: list[str]) -> None:
        files = _expand(paths)
        if not files:
            self.status.setText("No supported files to queue.")
            return
        self._go(NAV_QUEUE)
        self.status.setText(f"Detecting {len(files)} file(s)…")
        self.progress.setRange(0, 0); self.progress.show()
        worker = EnqueueWorker(self.queue, files)
        worker.signals.item_changed.connect(self._on_queue_item)
        worker.signals.finished.connect(self._on_enqueue_done)
        self.pool.start(worker)

    def _on_enqueue_done(self) -> None:
        self.progress.hide(); self.progress.setRange(0, 1)
        self._refresh_queue()
        self.status.setText(f"Queued {len(self.queue.items)} label(s).")

    def _on_queue_item(self, item: QueueItem) -> None:
        self._refresh_queue()

    def print_queue(self) -> None:
        pend = self.queue.pending()
        if not pend:
            self.status.setText("Nothing queued to print.")
            return
        self._sync()
        self.progress.setRange(0, 0); self.progress.show()
        self.status.setText(f"Printing {len(pend)} label(s)…")
        worker = BatchWorker(self.queue, routing_enabled=self.queue_route.isChecked())
        worker.signals.item_changed.connect(self._on_queue_item)
        worker.signals.finished.connect(self._on_batch_done)
        self.pool.start(worker)

    def _on_batch_done(self) -> None:
        self.progress.hide(); self.progress.setRange(0, 1)
        s = self.queue.summary()
        self.status.setText(f"Queue: {s.get(DONE,0)} done, {s.get(FAILED,0)} failed.")
        self._refresh_queue(); self._refresh_history(); self._update_nav_counts()

    def clear_queue(self) -> None:
        self.queue.items.clear()
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        while self.queue_lay.count() > 1:
            item = self.queue_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self.queue_empty.setVisible(not self.queue.items)
        p = theme.palette(self.dark)
        for it in self.queue.items:
            self.queue_lay.insertWidget(self.queue_lay.count() - 1, self._queue_row(it, p))
        s = self.queue.summary()
        self.queue_summary.setText(
            f"{s.get(QUEUED,0)} queued · {s.get(DONE,0)} done · {s.get(FAILED,0)} failed")
        self._update_nav_counts()

    def _queue_row(self, it: QueueItem, p: theme.Palette) -> QWidget:
        rowf = QFrame(); rowf.setObjectName("Row")
        lay = QHBoxLayout(rowf); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(12)
        thumb = QLabel()
        if it.label is not None:
            im = it.label.preview_image.copy(); im.thumbnail((44, 60), Image.LANCZOS)
            thumb.setPixmap(pil_to_qpixmap(im))
        thumb.setFixedWidth(48); lay.addWidget(thumb)
        info = QVBoxLayout(); info.setSpacing(2)
        title = QLabel(it.title); title.setStyleSheet("font-weight:600;")
        sub = QLabel(f"{it.carrier} · {it.tracking}" if it.tracking else (it.error or it.kind))
        sub.setObjectName("Tiny")
        info.addWidget(title); info.addWidget(sub)
        lay.addLayout(info, 1)
        chip_map = {QUEUED: (p.text_muted, p.chip_bg), PRINTING: (theme.BRAND, p.brand_tint),
                    DONE: (p.green, p.green_tint), FAILED: (p.red, p.red_tint)}
        fg, bg = chip_map.get(it.status, (p.text_muted, p.chip_bg))
        lay.addWidget(make_chip(it.status.title(), fg, bg))
        if it.status == FAILED and it.label is not None:
            rb = QPushButton("Retry"); rb.setObjectName("Ghost")
            rb.clicked.connect(lambda _c, item=it: self._retry(item))
            lay.addWidget(rb)
        return rowf

    def _retry(self, item: QueueItem) -> None:
        self.queue.retry(item, routing_enabled=self.queue_route.isChecked())
        self._refresh_queue(); self._refresh_history(); self._update_nav_counts()

    # ------------------------------------------------------------- history
    def _refresh_history(self) -> None:
        while self.history_lay.count() > 1:
            item = self.history_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        entries = self.history.recent(limit=200)
        self.history_empty.setVisible(not entries)
        p = theme.palette(self.dark)
        for e in entries:
            self.history_lay.insertWidget(self.history_lay.count() - 1, self._history_row(e, p))
        self._update_nav_counts()

    def _history_row(self, e: HistoryEntry, p: theme.Palette) -> QWidget:
        rowf = QFrame(); rowf.setObjectName("Row")
        lay = QHBoxLayout(rowf); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(12)
        thumb = QLabel()
        if e.thumb_png:
            pm = QPixmap()
            pm.loadFromData(e.thumb_png, "PNG")
            thumb.setPixmap(pm.scaled(44, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        thumb.setFixedWidth(48); lay.addWidget(thumb)
        info = QVBoxLayout(); info.setSpacing(2)
        when = _dt.datetime.fromtimestamp(e.created_at).strftime("%Y-%m-%d %H:%M")
        top = QLabel(f"{e.carrier} · {e.tracking or '—'}"); top.setStyleSheet("font-weight:600;")
        sub = QLabel(f"{when} · {e.source_name} · {e.printer}"); sub.setObjectName("Tiny")
        info.addWidget(top); info.addWidget(sub)
        lay.addLayout(info, 1)
        rb = QPushButton("Reprint"); rb.setObjectName("Ghost")
        rb.clicked.connect(lambda _c, entry=e: self.reprint(entry))
        lay.addWidget(rb)
        return rowf

    def reprint(self, entry: HistoryEntry) -> None:
        image = self.history.load_print_image(entry)
        if image is None:
            QMessageBox.warning(self, "PrintPal", "The stored image for this print is missing.")
            return
        printer = entry.printer or self.config.printer
        try:
            print_label(image, printer, copies=entry.copies or 1)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "PrintPal – print error", str(e))
            return
        self.status.setText(f"Reprinted {entry.tracking or entry.source_name} → {printer}.")

    def reprint_last(self) -> None:
        entry = self.history.last()
        if entry is None:
            self.status.setText("No print history yet.")
            return
        self.reprint(entry)

    def clear_history(self) -> None:
        if QMessageBox.question(self, "PrintPal", "Clear all print history?") == QMessageBox.Yes:
            self.history.clear()
            self._refresh_history(); self._update_nav_counts()

    # ------------------------------------------------------------- settings
    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.config)
        if dlg.exec():
            self._reload_printers()
            self.copies.setValue(self.config.copies)
            if self.labels:
                self.load_path(self.labels[0].source_path)

    # ------------------------------------------------------------- watchers
    def _start_watchers(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self) -> None:
        if self._busy:
            return
        try:
            job = ingest.claim_one()
        except Exception:
            job = None
        if job is not None:
            self.raise_(); self.activateWindow()
            if self.log:
                self.log.info("Ingesting spooled job %s (%s)", job.doc_path.name, job.origin)
            self.load_path(str(job.doc_path))

    # ------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        files = _expand(paths)
        if not files:
            return
        if len(files) == 1:
            self.load_path(files[0])
        else:
            self.enqueue(files)


def _validate(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return "That file could not be found."
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUPPORTED:
        return f"PrintPal works with PDF and image files, not {ext or 'this type'}."
    if ext == ".pdf":
        try:
            with open(path, "rb") as f:
                if not f.read(5).startswith(b"%PDF"):
                    return "This file has a .pdf name but isn't a valid PDF."
        except OSError as e:
            return f"Cannot read the file: {e}"
    return None


def _expand(paths: list[str]) -> list[str]:
    """Expand folders to their supported files; keep supported files as-is."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                fp = os.path.join(p, name)
                if os.path.isfile(fp) and os.path.splitext(name)[1].lower() in _SUPPORTED:
                    out.append(fp)
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in _SUPPORTED:
            out.append(p)
    return out
