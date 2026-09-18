"""App configuration. Stored as TOML in the user's AppData folder.

Only depends on the standard library so it loads instantly on cold start.
Reading is tolerant: unknown keys are ignored and bad values fall back to
defaults, so a hand-edited or older config never crashes the app.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "PrintPal"

DEFAULT_PRINTER = "DYMO LabelWriter 450"
DEFAULT_MEDIA = "4x6"
DEFAULT_DETECT_DPI = 200
DEFAULT_PRINT_DPI = 300
DEFAULT_MARGIN_INCHES = 0.08
DEFAULT_COPIES = 1
DEFAULT_AUTO_PRINT = False
DEFAULT_AUTO_PRINT_MIN_CONFIDENCE = 0.85
DEFAULT_SPLIT_NUP = True

_CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
CONFIG_PATH = _CONFIG_DIR / "config.toml"


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_bool(value, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return fallback


@dataclass
class Config:
    printer: str = DEFAULT_PRINTER
    # Smart routing (opt-in, never automatic): where 4x6 labels vs paper
    # documents go when the user enables routing for a job. Empty falls back to
    # `printer`.
    thermal_printer: str = ""
    paper_printer: str = ""
    media_size: str = DEFAULT_MEDIA
    detect_dpi: int = DEFAULT_DETECT_DPI
    print_dpi: int = DEFAULT_PRINT_DPI
    crop_margin_inches: float = DEFAULT_MARGIN_INCHES
    copies: int = DEFAULT_COPIES
    auto_print: bool = DEFAULT_AUTO_PRINT
    auto_print_min_confidence: float = DEFAULT_AUTO_PRINT_MIN_CONFIDENCE
    split_nup: bool = DEFAULT_SPLIT_NUP

    # Backwards-compatible alias: older configs and callers used `dpi` for the
    # detection raster resolution.
    @property
    def dpi(self) -> int:
        return self.detect_dpi

    @dpi.setter
    def dpi(self, value: int) -> None:
        self.detect_dpi = _as_int(value, DEFAULT_DETECT_DPI)

    def clamped(self) -> "Config":
        """Return self with values forced into sane ranges."""
        self.detect_dpi = max(100, min(400, self.detect_dpi))
        self.print_dpi = max(self.detect_dpi, min(600, self.print_dpi))
        self.crop_margin_inches = max(0.0, min(0.5, self.crop_margin_inches))
        self.copies = max(1, min(99, self.copies))
        self.auto_print_min_confidence = max(0.5, min(1.0, self.auto_print_min_confidence))
        return self

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.clamped()
        # Escape backslashes/quotes so a Windows printer name never breaks TOML.
        printer = self.printer.replace("\\", "\\\\").replace('"', '\\"')
        media = self.media_size.replace("\\", "\\\\").replace('"', '\\"')
        thermal = self.thermal_printer.replace("\\", "\\\\").replace('"', '\\"')
        paper = self.paper_printer.replace("\\", "\\\\").replace('"', '\\"')
        lines = [
            "# PrintPal configuration -- safe to edit by hand.",
            f'printer = "{printer}"',
            f'thermal_printer = "{thermal}"',
            f'paper_printer = "{paper}"',
            f'media_size = "{media}"',
            f"detect_dpi = {self.detect_dpi}",
            f"print_dpi = {self.print_dpi}",
            f"crop_margin_inches = {self.crop_margin_inches}",
            f"copies = {self.copies}",
            f"auto_print = {str(self.auto_print).lower()}",
            f"auto_print_min_confidence = {self.auto_print_min_confidence}",
            f"split_nup = {str(self.split_nup).lower()}",
        ]
        CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            cfg = cls()
            try:
                cfg.save()
            except OSError:
                pass
            return cfg
        try:
            data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return cls()
        # `dpi` is the historical key for the detection resolution.
        detect_dpi = _as_int(data.get("detect_dpi", data.get("dpi", DEFAULT_DETECT_DPI)),
                             DEFAULT_DETECT_DPI)
        return cls(
            printer=str(data.get("printer", DEFAULT_PRINTER)),
            thermal_printer=str(data.get("thermal_printer", "")),
            paper_printer=str(data.get("paper_printer", "")),
            media_size=str(data.get("media_size", DEFAULT_MEDIA)),
            detect_dpi=detect_dpi,
            print_dpi=_as_int(data.get("print_dpi", DEFAULT_PRINT_DPI), DEFAULT_PRINT_DPI),
            crop_margin_inches=_as_float(data.get("crop_margin_inches", DEFAULT_MARGIN_INCHES),
                                         DEFAULT_MARGIN_INCHES),
            copies=_as_int(data.get("copies", DEFAULT_COPIES), DEFAULT_COPIES),
            auto_print=_as_bool(data.get("auto_print", DEFAULT_AUTO_PRINT), DEFAULT_AUTO_PRINT),
            auto_print_min_confidence=_as_float(
                data.get("auto_print_min_confidence", DEFAULT_AUTO_PRINT_MIN_CONFIDENCE),
                DEFAULT_AUTO_PRINT_MIN_CONFIDENCE),
            split_nup=_as_bool(data.get("split_nup", DEFAULT_SPLIT_NUP), DEFAULT_SPLIT_NUP),
        ).clamped()
