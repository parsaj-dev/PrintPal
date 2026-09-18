"""Turn a file into ready-to-print labels.

This is the glue between detection and everything that consumes it (UI, auto
print). It is deliberately free of any tkinter or Windows imports so it can be
unit-tested headless, which is exactly how the detection quality is guarded.

A `ProcessedLabel` is one detected label on one page. It holds the detection
preview cheaply and renders the high-DPI print image only when asked, so paging
through a 20-page PDF stays instant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from printpal.config import Config
from printpal.detect import KIND_BLANK, LabelResult, find_label, _rotate_upright
from printpal.rasterize import (
    image_dpi, is_pdf, load_image, page_count, rasterize_pdf_region,
)

# Guard against someone copying a giant multi-hundred-page PDF by mistake.
MAX_PAGES = 50

ProgressCallback = Callable[[str, int, int], None]


def _apply_manual(img: Image.Image, degrees: int) -> Image.Image:
    degrees %= 360
    if degrees == 0:
        return img
    # PIL rotates counter-clockwise for positive angles; we want clockwise.
    return img.rotate(-degrees, expand=True)


@dataclass
class ProcessedLabel:
    source_path: str
    page_index: int
    page_count: int
    result: LabelResult
    manual_rotation: int = 0            # extra clockwise degrees (0/90/180/270)
    _print_cache: Image.Image | None = field(default=None, repr=False)

    # -- read-only conveniences ------------------------------------------------
    @property
    def confidence(self) -> float:
        return self.result.confidence

    @property
    def warnings(self) -> list[str]:
        return self.result.warnings

    @property
    def is_printable(self) -> bool:
        return self.result.kind != KIND_BLANK

    @property
    def kind(self) -> str:
        return self.result.kind

    @property
    def is_label(self) -> bool:
        """True for a shipping label; False for a packing slip / document page."""
        return self.result.is_label

    @property
    def preview_image(self) -> Image.Image:
        """Upright, cropped label at detection DPI, with any manual turn applied.
        Cheap -- use it for thumbnails and the on-screen preview."""
        return _apply_manual(self.result.image, self.manual_rotation)

    # -- manual rotation -------------------------------------------------------
    def rotate_cw(self) -> None:
        self.manual_rotation = (self.manual_rotation + 90) % 360
        self._print_cache = None

    def rotate_ccw(self) -> None:
        self.manual_rotation = (self.manual_rotation - 90) % 360
        self._print_cache = None

    def reset_rotation(self) -> None:
        self.manual_rotation = 0
        self._print_cache = None

    # -- print rendering -------------------------------------------------------
    def render_print_image(self, config: Config) -> Image.Image:
        """High-resolution, upright label ready for the spooler. Cached."""
        if self._print_cache is not None:
            return self._print_cache

        if is_pdf(self.source_path):
            base = rasterize_pdf_region(
                self.source_path, self.result.box,
                self.result.detect_dpi, config.print_dpi, page=self.page_index,
            )
            base = _rotate_upright(base, self.result.orientation)
        else:
            # Image files were detected at native resolution, so the preview
            # crop already is print quality.
            base = self.result.image

        self._print_cache = _apply_manual(base, self.manual_rotation)
        return self._print_cache


def process_file(path: str, config: Config,
                 progress: ProgressCallback | None = None) -> list[ProcessedLabel]:
    """Detect every printable label in `path`.

    Returns one ProcessedLabel per page that carries content. If every page is
    blank, the blank pages are returned so the caller can explain why.
    """
    total = page_count(path)
    capped = min(total, MAX_PAGES)
    labels: list[ProcessedLabel] = []

    for i in range(capped):
        if progress:
            progress(f"Reading page {i + 1} of {capped}…" if capped > 1
                     else "Reading label…", i, capped)
        img = load_image(path, dpi=config.detect_dpi, page=i)
        if progress:
            progress(f"Finding label{'' if capped == 1 else f' on page {i + 1}'}…",
                     i, capped)
        # PDFs are rendered at a known DPI; image files carry their own (or a
        # sensible default), so the physical-size regime and the inches read-out
        # stay honest instead of assuming the detection DPI.
        dpi = config.detect_dpi if is_pdf(path) else image_dpi(path, img)
        result = find_label(img, dpi=dpi, margin_inches=config.crop_margin_inches)
        labels.append(ProcessedLabel(path, i, capped, result))

    if total > MAX_PAGES:
        for lab in labels:
            lab.result.warnings.append(
                f"This file has {total} pages; only the first {MAX_PAGES} were scanned.")

    printable = [lab for lab in labels if lab.is_printable]
    return printable if printable else labels
