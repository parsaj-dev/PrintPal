"""Settings dialog for the Qt UI."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from printpal import autostart, routing
from printpal.config import Config

# Shown in the routing combos for "no dedicated printer": falls back to the
# main printer. Stored in the config as "".
_SAME = "(same as main printer)"


class SettingsDialog(QDialog):
    """``printers`` is the list the main window already loaded in the background,
    so opening Settings never waits on a (possibly slow) printer enumeration."""

    def __init__(self, parent, config: Config, printers: list[str]):
        super().__init__(parent)
        self.config = config
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
