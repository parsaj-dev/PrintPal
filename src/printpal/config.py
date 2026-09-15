"""App configuration. Stored as TOML in the user's AppData folder."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path


APP_NAME = "PrintPal"
DEFAULT_PRINTER = "DYMO LabelWriter 450"
DEFAULT_MEDIA = "4x6"
DEFAULT_DPI = 200
DEFAULT_MARGIN_INCHES = 0.06

_CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
CONFIG_PATH = _CONFIG_DIR / "config.toml"


@dataclass
class Config:
    printer: str = DEFAULT_PRINTER
    media_size: str = DEFAULT_MEDIA
    dpi: int = DEFAULT_DPI
    crop_margin_inches: float = DEFAULT_MARGIN_INCHES

    def save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f'printer = "{self.printer}"',
            f'media_size = "{self.media_size}"',
            f"dpi = {self.dpi}",
            f"crop_margin_inches = {self.crop_margin_inches}",
        ]
        CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cls(
            printer=data.get("printer", DEFAULT_PRINTER),
            media_size=data.get("media_size", DEFAULT_MEDIA),
            dpi=data.get("dpi", DEFAULT_DPI),
            crop_margin_inches=data.get("crop_margin_inches", DEFAULT_MARGIN_INCHES),
        )
