"""Settings dialog for the Qt UI."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from printpal import autostart, routing
from printpal.config import Config
from printpal.printing import PrintLayout

# Shown in the routing combos for "no dedicated printer": falls back to the
# main printer. Stored in the config as "".
_SAME = "(same as main printer)"


class SettingsDialog(QDialog):
    """``printers`` is the list the main window already loaded in the background,
    so opening Settings never waits on a (possibly slow) printer enumeration."""

    def __init__(self, parent, config: Config, printers: list[str], memory=None):
        super().__init__(parent)
        self.config = config
        self.memory = memory   # learn.CropMemory, for the "forget" button
        self.setWindowTitle("PrintPal Settings")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        printers = list(printers)
        for name in (config.printer, config.thermal_printer, config.paper_printer):
            if name and name not in printers:
                printers.append(name)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)
        title = QLabel("Settings")
        title.setObjectName("H1")
        root.addWidget(title)

        # Two columns keep the dialog short enough for a 1366x768 laptop screen.
        cols = QHBoxLayout()
        cols.setSpacing(28)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(8)
        right.setSpacing(8)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        root.addLayout(cols)

        # -- Printing ----------------------------------------------------------
        left.addWidget(_section("PRINTING"))
        f = _form()
        self.printer = _combo(printers, config.printer)
        f.addRow("Printer", self.printer)
        self.copies = QSpinBox()
        self.copies.setRange(1, 99)
        self.copies.setValue(config.copies)
        f.addRow("Default copies", self.copies)
        self.layout_btn = QPushButton()
        self.layout_btn.setObjectName("Ghost")
        self.layout_btn.setToolTip("Fit the page, a percentage, or a size in inches -- "
                                   "and where on the page the label goes")
        self.layout_btn.clicked.connect(self._open_layout)
        self._update_layout_btn()
        f.addRow("Size && position", self.layout_btn)
        left.addLayout(f)

        # -- Smart routing -----------------------------------------------------
        left.addWidget(_section("SMART ROUTING"))
        self.route_on = QCheckBox("Send labels and documents to different printers")
        self.route_on.setChecked(config.smart_routing)
        left.addWidget(self.route_on)
        f = _form()
        self.thermal = _combo([_SAME] + printers, config.thermal_printer or _SAME)
        self.paper = _combo([_SAME] + printers, config.paper_printer or _SAME)
        f.addRow("Labels to", self.thermal)
        f.addRow("Documents to", self.paper)
        left.addLayout(f)
        self.route_hint = QLabel()
        self.route_hint.setObjectName("Tiny")
        self.route_hint.setWordWrap(True)
        left.addWidget(self.route_hint)
        for w in (self.route_on, self.thermal, self.paper, self.printer):
            sig = w.toggled if isinstance(w, QCheckBox) else w.currentTextChanged
            sig.connect(self._update_route_hint)
        left.addStretch(1)

        # -- Automation --------------------------------------------------------
        right.addWidget(_section("AUTOMATION"))
        self.autoprint = QCheckBox("Auto-print a single confident label")
        self.autoprint.setChecked(config.auto_print)
        right.addWidget(self.autoprint)
        self.splitnup = QCheckBox("Split 2-up / 4-up pages into separate labels")
        self.splitnup.setChecked(config.split_nup)
        right.addWidget(self.splitnup)

        # -- Learning ----------------------------------------------------------
        right.addWidget(_section("LEARNING"))
        self.learn = QCheckBox("Remember my crop fixes for each label layout")
        self.learn.setToolTip("When you fix a crop with ✎ Adjust, the next label with "
                              "the same layout is cropped that way automatically.")
        self.learn.setChecked(config.learn_crops)
        right.addWidget(self.learn)
        row = QHBoxLayout()
        self.learn_count = QLabel()
        self.learn_count.setObjectName("Tiny")
        self.forget = QPushButton("Forget all")
        self.forget.setObjectName("Ghost")
        self.forget.clicked.connect(self._forget_all)
        row.addWidget(self.learn_count)
        row.addStretch(1)
        row.addWidget(self.forget)
        right.addLayout(row)
        self._update_learn_count()

        # -- Background --------------------------------------------------------
        right.addWidget(_section("SPEED"))
        self.background = QCheckBox("Keep running in the tray when closed")
        self.background.setToolTip("Prints to the PrintPal printer show up instantly "
                                   "instead of waiting for the app to start.")
        self.background.setChecked(config.run_in_background)
        right.addWidget(self.background)
        self.startup = QCheckBox("Start PrintPal in the tray when I sign in")
        self.startup.setEnabled(autostart.available())
        self.startup.setChecked(autostart.is_enabled())
        if not autostart.available():
            self.startup.setToolTip("Available in the installed app.")
        right.addWidget(self.startup)

        # -- Advanced ----------------------------------------------------------
        right.addWidget(_section("ADVANCED"))
        f = _form()
        self.detect = QSpinBox()
        self.detect.setRange(100, 400)
        self.detect.setSingleStep(10)
        self.detect.setValue(config.detect_dpi)
        self.detect.setToolTip("Lower is faster; below ~180 some barcodes stop reading.")
        f.addRow("Detection DPI", self.detect)
        self.printdpi = QSpinBox()
        self.printdpi.setRange(150, 600)
        self.printdpi.setSingleStep(50)
        self.printdpi.setValue(config.print_dpi)
        f.addRow("Print DPI", self.printdpi)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0.0, 0.5)
        self.margin.setSingleStep(0.02)
        self.margin.setDecimals(2)
        self.margin.setValue(config.crop_margin_inches)
        f.addRow("Crop margin (in)", self.margin)
        right.addLayout(f)
        right.addStretch(1)

        btns = QHBoxLayout()
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
        self._update_route_hint()

    def _update_layout_btn(self) -> None:
        lay = PrintLayout.from_config(self.config)
        text = "Fit page" if lay.mode == "fit" else lay.describe()
        if lay.anchor != "center":
            text += f" · {lay.anchor.replace('-', ' ')}"
        self.layout_btn.setText(text + "  …")

    def _open_layout(self) -> None:
        from printpal.qtui.layout import LayoutDialog
        printer = routing.printer_for("label", self._probe(), self.route_on.isChecked()).printer
        if LayoutDialog(self, self.config, printer, dark=self.config.dark_mode).exec():
            self._update_layout_btn()

    def _probe(self) -> Config:
        probe = Config(printer=self.printer.currentText().strip())
        probe.thermal_printer, probe.paper_printer = self._targets()
        probe.smart_routing = self.route_on.isChecked()
        return probe

    def _update_learn_count(self) -> None:
        n = len(self.memory) if self.memory is not None else 0
        self.learn_count.setText("Nothing learned yet." if n == 0 else
                                 f"{n} layout{'' if n == 1 else 's'} learned.")
        self.forget.setEnabled(n > 0)

    def _forget_all(self) -> None:
        if self.memory is None:
            return
        n = len(self.memory)
        ok = QMessageBox.question(
            self, "Forget learned crops",
            f"Forget the crops you taught PrintPal for {n} layout{'' if n == 1 else 's'}? "
            "Those labels go back to automatic detection.")
        if ok == QMessageBox.Yes:
            try:
                self.memory.clear()
            except OSError:
                pass
            self._update_learn_count()

    def _targets(self) -> tuple[str, str]:
        return _value(self.thermal), _value(self.paper)

    def _update_route_hint(self, *_a) -> None:
        on = self.route_on.isChecked()
        self.thermal.setEnabled(on)
        self.paper.setEnabled(on)
        probe = Config(printer=self.printer.currentText().strip())
        probe.thermal_printer, probe.paper_printer = self._targets()
        if not on:
            self.route_hint.setText("Off: everything prints to the main printer.")
        elif routing.routing_available(probe):
            self.route_hint.setText("Labels print cropped; packing slips and other "
                                    "documents print as the full page.")
        else:
            self.route_hint.setText("Pick two different printers for routing to "
                                    "take effect.")

    def _save(self) -> None:
        c = self.config
        c.printer = self.printer.currentText().strip()
        c.thermal_printer, c.paper_printer = self._targets()
        c.smart_routing = self.route_on.isChecked()
        c.copies = self.copies.value()
        c.auto_print = self.autoprint.isChecked()
        c.split_nup = self.splitnup.isChecked()
        c.learn_crops = self.learn.isChecked()
        c.run_in_background = self.background.isChecked()
        c.detect_dpi = self.detect.value()
        c.print_dpi = self.printdpi.value()
        c.crop_margin_inches = self.margin.value()
        c.clamped()
        try:
            c.save()
        except Exception:
            pass
        if autostart.available() and self.startup.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.startup.isChecked())
            except OSError:
                pass
        self.accept()


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Section")
    return lbl


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setSpacing(8)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return f


def _combo(items: list[str], value: str) -> QComboBox:
    c = QComboBox()
    c.addItems(items)
    if value:
        if c.findText(value) < 0:
            c.insertItem(0, value)
        c.setCurrentText(value)
    c.setMinimumWidth(220)
    return c


def _value(combo: QComboBox) -> str:
    text = combo.currentText().strip()
    return "" if text == _SAME else text
