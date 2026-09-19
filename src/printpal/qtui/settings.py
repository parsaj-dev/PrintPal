"""Settings dialog for the Qt UI."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from printpal.config import Config
from printpal.printing import list_printers


class SettingsDialog(QDialog):
    def __init__(self, parent, config: Config):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("PrintPal Settings")
        self.setMinimumWidth(440)

        try:
            printers = list_printers()
        except Exception:
            printers = []
        for name in (config.printer, config.thermal_printer, config.paper_printer):
            if name and name not in printers:
                printers = [name] + printers

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 20)
        root.setSpacing(14)
        title = QLabel("Settings")
        title.setObjectName("H1")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)

        def combo(value, items, editable=False):
            c = QComboBox()
            c.setEditable(editable)
            c.addItems(items)
            if value:
                if value not in items:
                    c.insertItem(0, value)
                c.setCurrentText(value)
            return c

        self.printer = combo(config.printer, printers)
        form.addRow("Printer", self.printer)

        route_hint = QLabel("Smart routing (used only when you enable it for a job)")
        route_hint.setObjectName("Muted")
        form.addRow(route_hint)
        self.thermal = combo(config.thermal_printer, [""] + printers)
        self.paper = combo(config.paper_printer, [""] + printers)
        form.addRow("Thermal (labels)", self.thermal)
        form.addRow("Paper (documents)", self.paper)

        self.media = combo(config.media_size,
                           ["4x6", "4x8", "2.25x1.25", "2.25x4", "4x2", "6x4"], editable=True)
        form.addRow("Media size", self.media)

        self.detect = QSpinBox(); self.detect.setRange(100, 400); self.detect.setSingleStep(10)
        self.detect.setValue(config.detect_dpi)
        form.addRow("Detection DPI", self.detect)

        self.printdpi = QSpinBox(); self.printdpi.setRange(150, 600); self.printdpi.setSingleStep(50)
        self.printdpi.setValue(config.print_dpi)
        form.addRow("Print DPI", self.printdpi)

        self.margin = QDoubleSpinBox(); self.margin.setRange(0.0, 0.5); self.margin.setSingleStep(0.02)
        self.margin.setDecimals(2); self.margin.setValue(config.crop_margin_inches)
        form.addRow("Crop margin (in)", self.margin)

        self.copies = QSpinBox(); self.copies.setRange(1, 99); self.copies.setValue(config.copies)
        form.addRow("Copies", self.copies)

        root.addLayout(form)

        self.autoprint = QCheckBox("Auto-print a single high-confidence label")
        self.autoprint.setChecked(config.auto_print)
        root.addWidget(self.autoprint)

        self.splitnup = QCheckBox("Split multi-label (N-up) pages into individual labels")
        self.splitnup.setChecked(config.split_nup)
        root.addWidget(self.splitnup)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save"); save.setObjectName("Primary")
        save.clicked.connect(self._save)
        btns.addWidget(cancel); btns.addWidget(save)
        root.addLayout(btns)

    def _save(self) -> None:
        c = self.config
        c.printer = self.printer.currentText().strip()
        c.thermal_printer = self.thermal.currentText().strip()
        c.paper_printer = self.paper.currentText().strip()
        c.media_size = self.media.currentText().strip()
        c.detect_dpi = self.detect.value()
        c.print_dpi = self.printdpi.value()
        c.crop_margin_inches = self.margin.value()
        c.copies = self.copies.value()
        c.auto_print = self.autoprint.isChecked()
        c.split_nup = self.splitnup.isChecked()
        c.clamped()
        try:
            c.save()
        except Exception:
            pass
        self.accept()
