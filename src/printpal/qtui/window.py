"""PrintPal main window (PySide6).

Everything slow -- detection, rendering, spooling, history writes, printer
enumeration -- runs on worker threads; the GUI thread only builds widgets. On an
old PC that is the difference between a window that keeps up and one that
freezes for a second after every print.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileSystemWatcher, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from PIL import Image

from printpal import ingest, routing
from printpal.batch import DONE, FAILED, PRINTING, PrintQueue, QUEUED, QueueItem
from printpal.carrier import parse_tracking
from printpal.config import Config
from printpal.history import History, HistoryEntry
from printpal.printing import list_printers, print_label
from printpal.qtui import theme
from printpal.qtui.settings import SettingsDialog
from printpal.qtui.widgets import PreviewCanvas, ThumbTile, make_chip, pil_to_qpixmap
from printpal.qtui.workers import (
    BatchWorker, DetectWorker, EnqueueWorker, PrintTask, PrintWorker,
)

if TYPE_CHECKING:
    from printpal.pipeline import ProcessedLabel

NAV_LABEL, NAV_QUEUE, NAV_HISTORY = 0, 1, 2

_SUPPORTED = (".pdf", ".xps", ".oxps", ".png", ".jpg", ".jpeg",
              ".tif", ".tiff", ".bmp", ".gif", ".webp")

# How often to re-check the spool if a file-system notification is ever missed.
# New jobs normally arrive via QFileSystemWatcher, within a few milliseconds.
_SPOOL_FALLBACK_MS = 1500

_HISTORY_ROWS = 100


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


class _Call(QRunnable):
    """Run a plain function on the thread pool (fire and forget)."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.fn()
        except Exception:  # noqa: BLE001
            pass


class MainWindow(QWidget):
    _printers_loaded = Signal(list)   # delivered from the background lister

    def __init__(self, config: Config, initial_path: str | None = None, log=None):
        super().__init__()
        self.config = config
        self.log = log
        self.dark = config.dark_mode
        self.pool = QThreadPool.globalInstance()
        # Prints go through a one-thread pool: jobs reach the spooler in the
        # order they were sent, and a burst of auto-prints never competes with
        # itself for an old PC's CPU.
        self.print_pool = QThreadPool(self)
        self.print_pool.setMaxThreadCount(1)
        self.history = History()
        self.queue = PrintQueue(config, print_label, history=self.history)

        self.labels: list[ProcessedLabel] = []
        self.selected = 0
        self._tiles: list[ThumbTile] = []
        self._queue_rows: dict[int, QWidget] = {}
        self._printers: list[str] = []
        self._workers: set = set()        # keep runnables' signal objects alive

        # Detection bookkeeping. Every detection gets a generation number; a
        # result whose generation is stale (cancelled / superseded) is ignored.
        self._gen = 0
        self._detecting = False
        self._loading_job: ingest.IngestJob | None = None   # spool job being detected
        self._current_job: ingest.IngestJob | None = None   # spool job on screen
        self._stale_jobs: list[ingest.IngestJob] = []       # to delete when idle
        self._prints_inflight = 0
        self._batch_running = False
        self._history_dirty = True
        self._quitting = False
        self._tray_hint_shown = False

        self.setObjectName("Root")
        self.setWindowTitle("PrintPal")
        self.setAcceptDrops(True)
        self.setMinimumSize(900, 620)
        self.resize(1040, 720)
        icon = _asset_path("icon.png")
        self._icon = QIcon(icon) if icon else QIcon()
        self.setWindowIcon(self._icon)

        self._build()
        self.apply_theme()
        self._build_tray()
        app = QApplication.instance()
        if app is not None:
            # Windows is logging off / shutting down: really close, don't hide
            # to the tray (which would hold up the session ending).
            app.commitDataRequest.connect(lambda _sm: setattr(self, "_quitting", True))

        QShortcut(QKeySequence("Ctrl+O"), self, self.open_files)
        QShortcut(QKeySequence("Ctrl+V"), self, self.paste_clipboard)
        QShortcut(QKeySequence("Ctrl+P"), self, self.print_current)
        QShortcut(QKeySequence("Ctrl+R"), self, self.reprint_last)
        QShortcut(QKeySequence("Ctrl+W"), self, self.close_label)
        QShortcut(QKeySequence("Escape"), self, self._escape)
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self._select_delta(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self._select_delta(1))

        self._show_empty()
        self._update_nav_counts()
        self._reload_printers()
        self._start_spool_watch()
        # The window is up; load the detection engine (OpenCV, NumPy, MuPDF,
        # zbar) in the background so the first label doesn't pay for it.
        threading.Thread(target=_warm_engine, daemon=True).start()

        if initial_path:
            QTimer.singleShot(0, lambda: self.load_path(initial_path))

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
        self.btn_open.setToolTip("Open label file(s)  (Ctrl+O)")
        self.btn_open.clicked.connect(self.open_files)
        self.btn_paste = QPushButton("Paste"); self.btn_paste.setObjectName("Ghost")
        self.btn_paste.setToolTip("Open the label file on the clipboard  (Ctrl+V)")
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
        if idx == NAV_HISTORY and self._history_dirty:
            self._refresh_history()

    def _update_nav_counts(self) -> None:
        q = len(self.queue.items)
        self.nav_btns[NAV_QUEUE].setText(f"Queue ({q})" if q else "Queue")
        try:
            n = self.history.count()
        except Exception:
            n = 0
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

        # info row: label facts on the left, the routing toggle on the right
        info_row = QHBoxLayout(); info_row.setSpacing(12)
        self.info = QHBoxLayout(); self.info.setSpacing(12)
        info_row.addLayout(self.info, 1)
        self.label_route = QCheckBox("Smart routing")
        self.label_route.setToolTip("Labels → label printer, documents → paper printer "
                                    "(printers are chosen in Settings)")
        self.label_route.toggled.connect(self._set_routing)
        info_row.addWidget(self.label_route)
        info_wrap = QWidget(); info_wrap.setLayout(info_row)
        lay.addWidget(info_wrap)

        # action bar
        bar = QFrame(); bar.setObjectName("Card")
        ab = QHBoxLayout(bar); ab.setContentsMargins(14, 10, 14, 10); ab.setSpacing(8)
        self.btn_close = QPushButton("✕  Close"); self.btn_close.setObjectName("Danger")
        self.btn_close.setToolTip("Close this label without printing  (Esc)")
        self.btn_close.clicked.connect(self.close_label)
        self.btn_ccw = QPushButton("↺"); self.btn_ccw.setObjectName("Tool"); self.btn_ccw.setToolTip("Rotate left")
        self.btn_ccw.clicked.connect(lambda: self._rotate(False))
        self.btn_cw = QPushButton("↻"); self.btn_cw.setObjectName("Tool"); self.btn_cw.setToolTip("Rotate right")
        self.btn_cw.clicked.connect(lambda: self._rotate(True))
        self.btn_save = QPushButton("Save…"); self.btn_save.setObjectName("Tool")
        self.btn_save.setToolTip("Save the cropped label as a PNG")
        self.btn_save.clicked.connect(self.save_current)
        for b in (self.btn_close, self.btn_ccw, self.btn_cw, self.btn_save):
            ab.addWidget(b)
        ab.addStretch(1)
        self.copies = QSpinBox(); self.copies.setRange(1, 99); self.copies.setValue(self.config.copies)
        self.copies.setPrefix("× "); self.copies.setToolTip("Copies")
        self.copies.setFixedWidth(72); ab.addWidget(self.copies)
        self.printer_combo = QComboBox(); self.printer_combo.setMinimumWidth(180)
        self.printer_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.printer_combo.setMinimumContentsLength(14)
        self.printer_combo.currentTextChanged.connect(self._on_printer_changed)
        ab.addWidget(self.printer_combo)
        self.btn_printall = QPushButton("Print all"); self.btn_printall.setObjectName("Ghost")
        self.btn_printall.clicked.connect(self.print_all_current)
        ab.addWidget(self.btn_printall)
        self.btn_print = QPushButton("Print  ▸"); self.btn_print.setObjectName("Primary")
        self.btn_print.setToolTip("Print this label  (Ctrl+P)")
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
        add = QPushButton("Add files…"); add.setObjectName("Ghost"); add.clicked.connect(self.open_files)
        self.btn_clearqueue = QPushButton("Clear"); self.btn_clearqueue.setObjectName("Ghost")
        self.btn_clearqueue.clicked.connect(self.clear_queue)
        self.btn_printqueue = QPushButton("Print all"); self.btn_printqueue.setObjectName("Primary")
        self.btn_printqueue.clicked.connect(self._print_or_stop_queue)
        head.addWidget(add); head.addWidget(self.btn_clearqueue); head.addWidget(self.btn_printqueue)
        lay.addLayout(head)

        self.queue_route = QCheckBox("Smart routing: labels → label printer, documents → paper printer")
        self.queue_route.toggled.connect(self._set_routing)
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
        lay = QHBoxLayout(w); lay.setContentsMargins(20, 6, 20, 6); lay.setSpacing(10)
        self.status = QLabel("Ready."); self.status.setObjectName("Status")
        lay.addWidget(self.status, 1)
        self.progress = QProgressBar(); self.progress.setFixedWidth(160); self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.btn_cancel = QPushButton("Cancel"); self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setToolTip("Stop what PrintPal is doing  (Esc)")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.hide()
        lay.addWidget(self.btn_cancel)
        return w

    def _set_working(self, on: bool, cancellable: bool = True) -> None:
        self.progress.setVisible(on)
        self.btn_cancel.setVisible(on and cancellable)

    # ---------------------------------------------------------------- tray
    def _build_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self._icon, self)
        self.tray.setToolTip("PrintPal")
        menu = QMenu(self)
        act_open = QAction("Open PrintPal", self); act_open.triggered.connect(self.show_window)
        act_quit = QAction("Quit PrintPal", self); act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_open); menu.addSeparator(); menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

    def _on_tray(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        if self.tray is not None:
            self.tray.hide()
        self.close()
        QApplication.quit()

    def closeEvent(self, e) -> None:
        if (not self._quitting and self.config.run_in_background
                and self.tray is not None and self.tray.isVisible()):
            # Keep running in the tray so the next print to PrintPal is instant.
            e.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(
                    "PrintPal is still running",
                    "It stays in the tray so labels you print show up instantly. "
                    "Right-click the icon to quit.",
                    QSystemTrayIcon.Information, 4000)
            return
        if self.tray is not None:
            self.tray.hide()
        e.accept()
        QApplication.quit()

    # --------------------------------------------------------------- theming
    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, self.dark)
        self.btn_theme.setText("☀" if self.dark else "☾")
        self.canvas.set_palette(theme.palette(self.dark))
        self._render_selected()

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        self.config.dark_mode = self.dark
        self._save_config()
        self.apply_theme()
        # Rows carry theme-coloured status chips; rebuild them in the new colours.
        self._rebuild_queue()
        self._history_dirty = True
        if self.stack.currentIndex() == NAV_HISTORY:
            self._refresh_history()

    def _save_config(self) -> None:
        try:
            self.config.save()
        except Exception:
            pass

    # -------------------------------------------------------------- printers
    def _reload_printers(self) -> None:
        """Show the saved printer immediately, then fill in the full list from a
        background thread -- enumerating printers can take seconds on an office
        PC with mapped network printers, and must not hold up the window."""
        self._fill_printers(self._printers)
        if not getattr(self, "_printers_hooked", False):
            self._printers_loaded.connect(self._fill_printers)
            self._printers_hooked = True

        def work():
            try:
                found = list_printers()
            except Exception:
                found = []
            _fix_missing_printer(self.config, found, self.log)
            self._printers_loaded.emit(found)
        threading.Thread(target=work, daemon=True).start()

    def _fill_printers(self, printers: list) -> None:
        self._printers = [p for p in printers if p]
        shown = list(self._printers)
        if self.config.printer and self.config.printer not in shown:
            shown = [self.config.printer] + shown
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear(); self.printer_combo.addItems(shown)
        if self.config.printer:
            self.printer_combo.setCurrentText(self.config.printer)
        self.printer_combo.blockSignals(False)
        self._sync_routing_ui()

    def _on_printer_changed(self, name: str) -> None:
        if name and name != self.config.printer:
            self.config.printer = name
            self._save_config()
            self._sync_routing_ui()
            self._prerender_selected()

    # --------------------------------------------------------------- routing
    def _routing_on(self) -> bool:
        return routing.routing_active(self.config)

    def _set_routing(self, on: bool) -> None:
        if on == self.config.smart_routing:
            return
        self.config.smart_routing = on
        self._save_config()
        self._sync_routing_ui()
        self._prerender_selected()

    def _sync_routing_ui(self) -> None:
        """Routing toggles mirror the one saved setting. They are only offered
        once two different printers are set -- until then routing can't do
        anything, and an enabled-but-inert checkbox is just confusing."""
        available = routing.routing_available(self.config)
        on = self.config.smart_routing and available
        for cb in (self.label_route, self.queue_route):
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.setEnabled(available)
            cb.blockSignals(False)
        self.label_route.setVisible(available)
        self.queue_route.setToolTip(
            "" if available else "Choose a label printer and a paper printer in Settings first.")
        # With routing on, each label's destination is shown next to it and the
        # printer box is just the fallback -- say so.
        self.printer_combo.setToolTip(
            "Fallback printer (smart routing picks the printer per label)" if on
            else "Printer")
        if self.labels:
            self._render_selected()

    def _decision(self, lab: ProcessedLabel) -> routing.RoutingDecision:
        return routing.printer_for(lab.kind, self.config, self._routing_on())

    # ------------------------------------------------------------- input
    def open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose label(s)", "",
            "Label files (*.pdf *.xps *.oxps *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp);;"
            "All files (*.*)")
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

    def load_path(self, path: str, job: ingest.IngestJob | None = None) -> None:
        """Detect ``path`` in the background. A newer load replaces one that is
        still running (its result is dropped), so nothing the user opens is ever
        silently ignored."""
        err = _validate(path)
        if err:
            # Leaves any detection already running untouched.
            self.status.setText(err)
            if job is not None:
                self._stale_jobs.append(job)
                self._sweep_jobs()
            else:
                QMessageBox.warning(self, "PrintPal", err)
            return
        if self._detecting and self._loading_job is not None:
            self._stale_jobs.append(self._loading_job)
        self._gen += 1
        self._detecting = True
        self._loading_job = job
        self._go(NAV_LABEL)
        self.status.setText(f"Reading {os.path.basename(path)}…")
        self._set_working(True)
        worker = DetectWorker(path, self.config, self._gen, prerender=self._prerender_first)
        # Cross-thread signals -> main-thread slots (Qt queues them automatically).
        worker.signals.done.connect(self._on_detect_done)
        worker.signals.error.connect(self._on_detect_error)
        worker.signals.progress.connect(self._on_detect_progress)
        self._start(worker)

    def _start(self, worker) -> None:
        self._workers.add(worker)
        self.pool.start(worker)

    def _done_with(self, worker) -> None:
        self._workers.discard(worker)

    def _on_detect_progress(self, gen: int, msg: str) -> None:
        if gen == self._gen and self._detecting:
            self.status.setText(msg)

    def _finish_detect(self) -> None:
        self._detecting = False
        if not self._batch_running:
            self._set_working(False)
        # Drain the next spooled job right away instead of waiting for a poll.
        QTimer.singleShot(0, self._poll)

    def _on_detect_done(self, gen: int, labels: list) -> None:
        self._drop_finished_workers()
        if gen != self._gen:
            return   # cancelled or replaced by a newer load
        job, self._loading_job = self._loading_job, None
        self._finish_detect()
        printable = [l for l in labels if l.is_printable]
        if not printable:
            if job is not None:
                self._stale_jobs.append(job)
            self.status.setText("No label found in that file — it looks blank.")
            self._sweep_jobs()
            return
        self._replace_current_job(job)
        self.labels = printable
        self.selected = 0
        self._show_results()
        n = sum(1 for l in self.labels if l.is_label)
        if n == 0:
            self.status.setText(f"No shipping label detected — showing {len(self.labels)} page(s).")
        else:
            self.status.setText(f"Found {n} label{'s' if n != 1 else ''} · "
                                f"{int(self.labels[0].confidence * 100)}% confident")
        lab = self.labels[0]
        if (self.config.auto_print and len(self.labels) == 1 and lab.is_label
                and lab.confidence >= self.config.auto_print_min_confidence):
            self._print_labels([lab], auto=True)

    def _on_detect_error(self, gen: int, msg: str) -> None:
        self._drop_finished_workers()
        if gen != self._gen:
            return
        if self._loading_job is not None:
            self._stale_jobs.append(self._loading_job)
            self._loading_job = None
        self._finish_detect()
        self._sweep_jobs()
        self.status.setText("Couldn't read that file.")
        QMessageBox.warning(self, "PrintPal", f"Couldn't read that file.\n\n{msg}")

    def _drop_finished_workers(self) -> None:
        self._workers = {w for w in self._workers if not isinstance(w, DetectWorker)
                         or w.generation >= self._gen}

    # ------------------------------------------------------------- cancel / close
    def _escape(self) -> None:
        if self._detecting or self._batch_running:
            self.cancel()
        elif self.stack.currentIndex() == NAV_LABEL and self.labels:
            self.close_label()

    def cancel(self) -> None:
        """Stop the current detection (its result is discarded) and/or the
        running batch (after the label currently printing)."""
        if self._detecting:
            self._gen += 1
            if self._loading_job is not None:
                self._stale_jobs.append(self._loading_job)
                self._loading_job = None
            self._detecting = False
            self.status.setText("Cancelled.")
            QTimer.singleShot(0, self._poll)
        if self._batch_running:
            self.queue.cancel()
            self.status.setText("Stopping after the current label…")
            self.btn_printqueue.setEnabled(False)
        if not self._batch_running:
            self._set_working(False)
        self._sweep_jobs()

    def close_label(self) -> None:
        """Dismiss the label(s) on screen without printing."""
        if not self.labels:
            return
        self.labels = []
        self.selected = 0
        self._replace_current_job(None)
        self._show_empty()
        self.status.setText("Label closed.")

    def _replace_current_job(self, job: ingest.IngestJob | None) -> None:
        if self._current_job is not None and self._current_job is not job:
            self._stale_jobs.append(self._current_job)
        self._current_job = job
        self._sweep_jobs()

    def _sweep_jobs(self) -> None:
        """Delete spooled documents that are no longer on screen -- but only while
        nothing could still be reading them (a detection or a print render)."""
        if not self._stale_jobs or self._detecting or self._prints_inflight:
            return
        jobs, self._stale_jobs = self._stale_jobs, []
        for job in jobs:
            try:
                ingest.complete(job)
            except Exception:
                pass

    # ------------------------------------------------------------- label view state
    def _show_empty(self) -> None:
        self.label_view.setCurrentIndex(0)
        self.canvas.set_image(None)
        for t in self._tiles:
            t.setParent(None)
        self._tiles = []

    def _show_results(self) -> None:
        self.label_view.setCurrentIndex(1)
        self.copies.setValue(self.config.copies)
        self._build_rail()
        self.btn_printall.setVisible(len(self.labels) > 1)
        self._update_print_buttons()
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
            tile = ThumbTile(i, self._thumb(lab), f"Label {i + 1}" if lab.is_label else f"Page {lab.page_index + 1}")
            tile.clicked.connect(self._select)
            self.rail_lay.insertWidget(self.rail_lay.count() - 1, tile)
            self._tiles.append(tile)
        self._highlight_rail()

    @staticmethod
    def _thumb(lab: ProcessedLabel) -> QPixmap:
        thumb = lab.preview_image.copy()
        thumb.thumbnail((120, 150), Image.BILINEAR)
        return pil_to_qpixmap(thumb)

    def _highlight_rail(self) -> None:
        for i, t in enumerate(self._tiles):
            t.set_selected(i == self.selected)

    def _select(self, idx: int) -> None:
        if 0 <= idx < len(self.labels) and idx != self.selected:
            self.selected = idx
            self._render_selected()
            self._prerender_selected()

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
        if lab.is_label:
            text, fg, bg = theme.confidence_style(r.confidence, p)
            self.info.addWidget(make_chip(f"{text} · {int(r.confidence * 100)}%", fg, bg))
        else:
            self.info.addWidget(make_chip("Document", p.text_muted, p.chip_bg))
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
        if self._routing_on():
            d = self._decision(lab)
            where = "full page" if d.full_page else "cropped"
            self.info.addWidget(make_chip(f"→ {d.printer} ({where})", theme.BRAND, p.brand_tint))
        self.info.addStretch(1)
        if r.warnings:
            warn = QLabel("⚠  " + r.warnings[0]); warn.setWordWrap(True)
            warn.setObjectName("Warn")
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
            self._tiles[self.selected].set_pixmap(self._thumb(lab))
        self._render_selected()
        self._prerender_selected()

    # ------------------------------------------------------------- pre-render
    def _prerender(self, lab: ProcessedLabel) -> None:
        """Render ``lab``'s print image now (worker thread) so Print is instant."""
        try:
            lab.render_print_image(self.config, full_page=self._decision(lab).full_page)
        except Exception:
            pass   # the real print will surface the error

    def _prerender_first(self, labels: list) -> None:
        printable = [l for l in labels if l.is_printable]
        if printable:
            self._prerender(printable[0])

    def _prerender_selected(self) -> None:
        if self.labels:
            self.pool.start(_Call(partial(self._prerender, self.labels[self.selected])))

    # ------------------------------------------------------------- printing (label view)
    def print_current(self) -> None:
        if not self.labels or self.label_view.currentIndex() != 1:
            return
        self._print_labels([self.labels[self.selected]])

    def print_all_current(self) -> None:
        if self.labels:
            self._print_labels([l for l in self.labels if l.is_printable])

    def _print_labels(self, labels: list, auto: bool = False) -> None:
        # A manual print waits for the one in flight (no double-click duplicates);
        # auto-prints always go -- they queue up behind it in order.
        if not labels or (self._prints_inflight and not auto):
            return
        copies = self.copies.value()
        tasks, targets = [], []
        for lab in labels:
            d = self._decision(lab)
            if not d.printer:
                QMessageBox.warning(self, "PrintPal", "Choose a printer first.")
                return
            targets.append(d.printer)
            tasks.append(PrintTask(
                render=partial(lab.render_print_image, self.config, d.full_page),
                printer=d.printer, copies=copies,
                after=partial(self._record_history, lab, d.printer, copies)))
        dest = ", ".join(dict.fromkeys(targets))
        self.status.setText(("Auto-printing" if auto else "Printing") + f" to {dest}…")
        self._run_print(tasks, dest, copies, interactive=not auto)

    def _run_print(self, tasks: list[PrintTask], dest: str, copies: int,
                   interactive: bool = True) -> None:
        self._prints_inflight += 1
        self._update_print_buttons()
        worker = PrintWorker(tasks, print_label)
        worker.signals.finished.connect(
            lambda ok, errors, w=worker: self._on_print_done(w, ok, errors, len(tasks),
                                                             dest, copies, interactive))
        self._workers.add(worker)
        self.print_pool.start(worker)

    def _on_print_done(self, worker, ok: int, errors: list, total: int, dest: str,
                       copies: int, interactive: bool) -> None:
        self._done_with(worker)
        self._prints_inflight = max(0, self._prints_inflight - 1)
        self._update_print_buttons()
        if errors:
            self.status.setText(f"Print failed: {errors[0]}")
            if interactive or ok == 0:
                QMessageBox.critical(self, "PrintPal – print error", "\n\n".join(errors[:3]))
        elif total == 1:
            self.status.setText(f"Sent to {dest}" + (f" × {copies}" if copies > 1 else "") + ".")
        else:
            self.status.setText(f"Printed {ok} of {total} to {dest}.")
        if ok:
            self._history_dirty = True
            if self.stack.currentIndex() == NAV_HISTORY:
                self._refresh_history()
            self._update_nav_counts()
        self._sweep_jobs()

    def _update_print_buttons(self) -> None:
        busy = self._prints_inflight > 0
        self.btn_print.setEnabled(not busy)
        self.btn_printall.setEnabled(not busy)
        self.btn_print.setText("Printing…" if busy else "Print  ▸")

    def _record_history(self, lab: ProcessedLabel, printer: str, copies: int,
                        image: Image.Image) -> None:
        """Runs on the print worker thread."""
        ship = parse_tracking(lab.result.barcode_data)
        entry = HistoryEntry(
            created_at=0.0, source_name=os.path.basename(lab.source_path),
            carrier=ship.carrier, tracking=ship.tracking, kind=lab.kind,
            copies=copies, printer=printer,
            page_index=lab.page_index, region_index=lab.region_index,
            dpi=self.config.print_dpi)
        self.history.record(entry, image)

    def save_current(self) -> None:
        if not self.labels:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save cropped label", "label.png",
                                              "PNG image (*.png)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.labels[self.selected].render_print_image(self.config).save(path)
            self.status.setText(f"Saved {os.path.basename(path)}.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "PrintPal", f"Couldn't save.\n\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------- queue
    def enqueue(self, paths: list[str]) -> None:
        files = _expand(paths)
        if not files:
            self.status.setText("No supported files to queue.")
            return
        self._go(NAV_QUEUE)
        self.status.setText(f"Detecting {len(files)} file(s)…")
        self._set_working(True, cancellable=False)
        worker = EnqueueWorker(self.queue, files)
        worker.signals.item_changed.connect(self._on_queue_item)
        worker.signals.finished.connect(lambda w=worker: self._on_enqueue_done(w))
        self._start(worker)

    def _on_enqueue_done(self, worker) -> None:
        self._done_with(worker)
        if not (self._detecting or self._batch_running):
            self._set_working(False)
        self._update_queue_summary()
        self.status.setText(f"Queued {len(self.queue.items)} label(s).")

    def _print_or_stop_queue(self) -> None:
        if self._batch_running:
            self.cancel()
        else:
            self.print_queue()

    def print_queue(self, item: QueueItem | None = None) -> None:
        if self._batch_running:
            return
        if item is None and not self.queue.pending():
            self.status.setText("Nothing queued to print.")
            return
        n = 1 if item is not None else len(self.queue.pending())
        self._batch_running = True
        self._set_working(True)
        self.btn_printqueue.setText("Stop")
        self.btn_clearqueue.setEnabled(False)
        self.status.setText(f"Printing {n} label(s)…")
        worker = BatchWorker(self.queue, routing_enabled=self._routing_on(), item=item)
        worker.signals.item_changed.connect(self._on_queue_item)
        worker.signals.finished.connect(lambda w=worker: self._on_batch_done(w))
        self._start(worker)

    def _on_batch_done(self, worker) -> None:
        self._done_with(worker)
        self._batch_running = False
        self.btn_printqueue.setText("Print all")
        self.btn_printqueue.setEnabled(True)
        self.btn_clearqueue.setEnabled(True)
        if not self._detecting:
            self._set_working(False)
        s = self.queue.summary()
        self.status.setText(f"Queue: {s.get(DONE, 0)} done, {s.get(FAILED, 0)} failed"
                            + (f", {s.get(QUEUED, 0)} not printed." if s.get(QUEUED) else "."))
        self._rebuild_queue()
        self._history_dirty = True
        self._update_nav_counts()

    def clear_queue(self) -> None:
        if self._batch_running:
            return
        self.queue.clear()
        self._rebuild_queue()

    def _remove_queue_item(self, item: QueueItem) -> None:
        if item.status == PRINTING:
            return
        self.queue.remove(item)
        row = self._queue_rows.pop(item.id, None)
        if row is not None:
            row.setParent(None)
        self._update_queue_summary()

    def _on_queue_item(self, item: QueueItem) -> None:
        """Update just the one row that changed -- rebuilding every row (and
        its thumbnail) per update is quadratic on a long batch."""
        if item not in self.queue.items:
            return
        new = self._queue_row(item, theme.palette(self.dark))
        old = self._queue_rows.get(item.id)
        if old is not None:
            idx = self.queue_lay.indexOf(old)
            old.setParent(None)
            self.queue_lay.insertWidget(idx, new)
        else:
            self.queue_lay.insertWidget(self.queue_lay.count() - 1, new)
        self._queue_rows[item.id] = new
        self._update_queue_summary()

    def _rebuild_queue(self) -> None:
        for row in self._queue_rows.values():
            row.setParent(None)
        self._queue_rows = {}
        p = theme.palette(self.dark)
        for it in self.queue.items:
            row = self._queue_row(it, p)
            self.queue_lay.insertWidget(self.queue_lay.count() - 1, row)
            self._queue_rows[it.id] = row
        self._update_queue_summary()

    def _update_queue_summary(self) -> None:
        self.queue_empty.setVisible(not self.queue.items)
        s = self.queue.summary()
        self.queue_summary.setText(
            f"{s.get(QUEUED, 0)} queued · {s.get(DONE, 0)} done · {s.get(FAILED, 0)} failed")
        self._update_nav_counts()

    def _queue_row(self, it: QueueItem, p: theme.Palette) -> QWidget:
        rowf = QFrame(); rowf.setObjectName("Row")
        lay = QHBoxLayout(rowf); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(12)
        thumb = QLabel()
        if it.label is not None:
            im = it.label.preview_image.copy(); im.thumbnail((44, 60), Image.BILINEAR)
            thumb.setPixmap(pil_to_qpixmap(im))
        thumb.setFixedWidth(48); lay.addWidget(thumb)
        info = QVBoxLayout(); info.setSpacing(2)
        title = QLabel(it.title); title.setStyleSheet("font-weight:600;")
        sub_text = f"{it.carrier} · {it.tracking}" if it.tracking else (it.error or it.kind)
        if it.printer and it.status in (DONE, PRINTING):
            sub_text += f"  →  {it.printer}"
        elif it.label is not None and it.status == QUEUED and self._routing_on():
            sub_text += f"  →  {routing.printer_for(it.kind, self.config, True).printer}"
        sub = QLabel(sub_text)
        sub.setObjectName("Tiny")
        info.addWidget(title); info.addWidget(sub)
        lay.addLayout(info, 1)
        chip_map = {QUEUED: (p.text_muted, p.chip_bg), PRINTING: (theme.BRAND, p.brand_tint),
                    DONE: (p.green, p.green_tint), FAILED: (p.red, p.red_tint)}
        fg, bg = chip_map.get(it.status, (p.text_muted, p.chip_bg))
        lay.addWidget(make_chip(it.status.title(), fg, bg), 0, Qt.AlignVCenter)
        if it.status == FAILED and it.label is not None:
            rb = QPushButton("Retry"); rb.setObjectName("Ghost")
            rb.clicked.connect(lambda _c, item=it: self.print_queue(item))
            lay.addWidget(rb)
        if it.status != PRINTING:
            xb = QPushButton("✕"); xb.setObjectName("IconBtn"); xb.setToolTip("Remove from queue")
            xb.clicked.connect(lambda _c, item=it: self._remove_queue_item(item))
            lay.addWidget(xb)
        return rowf

    # ------------------------------------------------------------- history
    def _refresh_history(self) -> None:
        self._history_dirty = False
        while self.history_lay.count() > 1:
            item = self.history_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        try:
            entries = self.history.recent(limit=_HISTORY_ROWS)
        except Exception:
            entries = []
        self.history_empty.setVisible(not entries)
        for e in entries:
            self.history_lay.insertWidget(self.history_lay.count() - 1, self._history_row(e))
        self._update_nav_counts()

    def _history_row(self, e: HistoryEntry) -> QWidget:
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
        if self._prints_inflight:
            self.status.setText("Still printing — try again in a moment.")
            return
        printer = entry.printer or self.config.printer

        def render():
            image = self.history.load_print_image(entry)
            if image is None:
                raise RuntimeError("The stored image for this print is missing.")
            return image
        label = entry.tracking or entry.source_name
        self.status.setText(f"Reprinting {label} → {printer}…")
        self._run_print([PrintTask(render=render, printer=printer, copies=entry.copies or 1)],
                        printer, entry.copies or 1)

    def reprint_last(self) -> None:
        entry = self.history.last()
        if entry is None:
            self.status.setText("No print history yet.")
            return
        self.reprint(entry)

    def clear_history(self) -> None:
        if QMessageBox.question(self, "PrintPal", "Clear all print history?") == QMessageBox.Yes:
            self.history.clear()
            self._refresh_history()

    # ------------------------------------------------------------- settings
    def open_settings(self) -> None:
        before = (self.config.detect_dpi, self.config.crop_margin_inches, self.config.split_nup)
        dlg = SettingsDialog(self, self.config, self._printers)
        if not dlg.exec():
            return
        self._fill_printers(self._printers)
        self.copies.setValue(self.config.copies)
        after = (self.config.detect_dpi, self.config.crop_margin_inches, self.config.split_nup)
        if self.labels and before != after:
            # Detection settings changed: re-run it on the file on screen.
            self.load_path(self.labels[0].source_path, job=self._current_job)
            self._current_job = None
        elif self.labels:
            for lab in self.labels:   # print DPI may have changed
                lab.reset_render_cache()
            self._render_selected()
            self._prerender_selected()

    # ------------------------------------------------------------- spool watch
    def _start_spool_watch(self) -> None:
        """Pick up jobs from the virtual printer (or a second launch) the moment
        they land: a file-system notification wakes us immediately; a slow timer
        is only a safety net."""
        try:
            ingest.SPOOL_DIR.mkdir(parents=True, exist_ok=True)
            self._fs_watch = QFileSystemWatcher([str(ingest.SPOOL_DIR)], self)
            self._fs_watch.directoryChanged.connect(lambda _p: QTimer.singleShot(20, self._poll))
        except Exception:
            self._fs_watch = None
        self._timer = QTimer(self)
        self._timer.setInterval(_SPOOL_FALLBACK_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        QTimer.singleShot(0, self._poll)

    def _poll(self) -> None:
        try:
            if ingest.take_show_request():
                self.show_window()
        except Exception:
            pass
        if self._detecting:
            return   # drained again as soon as this detection finishes
        try:
            job = ingest.claim_one()
        except Exception:
            job = None
        if job is not None:
            self.show_window()
            if self.log:
                self.log.info("Ingesting spooled job %s (%s)", job.doc_path.name, job.origin)
            self.load_path(str(job.doc_path), job=job)

    # ------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        files = _expand(paths)
        if not files:
            self.status.setText("PrintPal works with PDF, XPS and image files.")
            return
        if len(files) == 1:
            self.load_path(files[0])
        else:
            self.enqueue(files)


def _fix_missing_printer(config: Config, found: list[str], log=None) -> None:
    """If the saved printer isn't installed (e.g. the DYMO default on a PC with a
    Rollo), switch to the Windows default printer. Runs on the lister thread."""
    if sys.platform != "win32" or not found or not config.printer or config.printer in found:
        return
    try:
        from printpal.printing import printer_exists
        if printer_exists(config.printer):
            return   # reachable, just not enumerated (some shared printers)
    except Exception:
        pass
    if log:
        log.info("Configured printer %r missing; using %r", config.printer, found[0])
    config.printer = found[0]   # list_printers() puts the Windows default first
    try:
        config.save()
    except Exception:
        pass


def _warm_engine() -> None:
    try:
        import printpal.pipeline  # noqa: F401  (pulls in cv2/numpy/pymupdf/pyzbar)
    except Exception:
        pass  # a real error will surface properly on the first detection


def _validate(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return "That file could not be found."
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUPPORTED:
        return f"PrintPal works with PDF, XPS and image files, not {ext or 'this type'}."
    if ext == ".pdf":
        try:
            with open(path, "rb") as f:
                # The PDF spec allows junk before the header; look a little way in.
                if b"%PDF" not in f.read(1024):
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
