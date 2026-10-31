"""Turn a file into ready-to-print labels.

This is the glue between detection and everything that consumes it (UI, auto
print). It is deliberately free of any tkinter or Windows imports so it can be
unit-tested headless, which is exactly how the detection quality is guarded.

A `ProcessedLabel` is one detected label on one page. It holds the detection
preview cheaply and renders the high-DPI print image only when asked, so paging
through a 20-page PDF stays instant.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from printpal import learn
from printpal.config import Config
from printpal.detect import KIND_BLANK, LabelResult, find_labels, orientation_for, _rotate_upright
from printpal.rasterize import (
    image_dpi, is_document, iter_pages, load_image, rasterize_pdf, rasterize_pdf_region,
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
    region_index: int = 0               # which label on the page (N-up: 0,1,2,...)
    region_count: int = 1               # labels found on this page
    manual_rotation: int = 0            # extra clockwise degrees (0/90/180/270)
    # Layout fingerprint (set when crop memory is on; None for N-up cells), and
    # the name of the learned layout whose crop is in use, if any.
    fingerprint: learn.Fingerprint | None = field(default=None, repr=False)
    learned: str | None = None
    # The automatic crop, kept once something replaces it so Adjust can go back.
    _auto: tuple | None = field(default=None, repr=False)
    _print_cache: Image.Image | None = field(default=None, repr=False)
    _page_cache: Image.Image | None = field(default=None, repr=False)
    # Print images are rendered on worker threads (a background pre-render right
    # after detection, then the print job); the lock makes the second caller wait
    # for -- and reuse -- the first one's render instead of doing it twice.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

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
        self._set_rotation(self.manual_rotation + 90)

    def rotate_ccw(self) -> None:
        self._set_rotation(self.manual_rotation - 90)

    def reset_rotation(self) -> None:
        self._set_rotation(0)

    def reset_render_cache(self) -> None:
        """Forget rendered print images (e.g. after the print DPI changed)."""
        with self._lock:
            self._print_cache = None
            self._page_cache = None

    # -- choosing a different area --------------------------------------------
    def page_image(self) -> Image.Image:
        """The whole source page at the detection DPI -- the coordinate space of
        ``result.box`` and ``result.candidates``. Rendered on demand (it is only
        needed when the user adjusts the crop)."""
        if is_document(self.source_path):
            return rasterize_pdf(self.source_path, self.result.detect_dpi, page=self.page_index)
        return load_image(self.source_path)

    @property
    def auto_box(self) -> tuple[int, int, int, int]:
        """The automatic crop (with margin), even after it has been replaced."""
        return self._auto[0] if self._auto else self.result.box

    def restore_auto(self) -> None:
        """Go back to the automatic crop exactly as detection produced it."""
        if self._auto is None:
            return
        r = self.result
        with self._lock:
            (r.box, r.orientation, r.image, r.method, r.confidence,
             r.is_full_page, r.warnings) = self._auto
            r.warnings = list(r.warnings)
            self._auto = None
            self.learned = None
            self.manual_rotation = 0
            self._print_cache = None
            self._page_cache = None

    def apply_box(self, box: tuple[int, int, int, int], page: Image.Image,
                  margin_px: int = 0, *, method: str = "manual",
                  confidence: float = 1.0, warnings: list[str] | None = None) -> None:
        """Use ``box`` (page px at detect DPI) as the crop instead of the detected
        one: re-crop, re-derive the upright rotation from the barcodes inside it,
        and drop cached print renders."""
        W, H = page.size
        x0, y0, x1, y1 = (int(v) for v in box)
        x0, y0 = max(0, x0 - margin_px), max(0, y0 - margin_px)
        x1, y1 = min(W, x1 + margin_px), min(H, y1 + margin_px)
        if x1 - x0 < 4 or y1 - y0 < 4:
            raise ValueError("That area is too small to print.")
        box = (x0, y0, x1, y1)
        r = self.result
        full = box == (0, 0, W, H)
        # A whole page keeps the page's own orientation (its text reads as the
        # sender laid it out); a smaller area turns upright with its barcodes.
        orientation = "UP" if full else orientation_for(box, r.barcode_boxes, default="UP")
        with self._lock:
            if self._auto is None:
                self._auto = (r.box, r.orientation, r.image, r.method, r.confidence,
                              r.is_full_page, list(r.warnings))
            r.box = box
            r.orientation = orientation
            r.image = _rotate_upright(page.crop(box), orientation)
            r.method = method
            r.confidence = confidence
            r.is_full_page = full
            r.warnings = list(warnings or [])
            self.manual_rotation = 0
            self._print_cache = None
            self._page_cache = None

    def _set_rotation(self, degrees: int) -> None:
        with self._lock:
            self.manual_rotation = degrees % 360
            self._print_cache = None
            self._page_cache = None

    # -- print rendering -------------------------------------------------------
    def render_print_image(self, config: Config, full_page: bool = False) -> Image.Image:
        """High-resolution, upright label ready for the spooler. Cached.

        ``full_page`` renders the whole source page instead of the detected crop
        (used when a document is routed to a paper printer)."""
        with self._lock:
            if full_page:
                if self._page_cache is None:
                    self._page_cache = self._tag(_apply_manual(self._render_page(config),
                                                               self.manual_rotation), config)
                return self._page_cache
            if self._print_cache is None:
                self._print_cache = self._tag(_apply_manual(self._render_crop(config),
                                                            self.manual_rotation), config)
            return self._print_cache

    def print_dpi(self, config: Config) -> int:
        """Resolution of ``render_print_image``: documents render at the print
        DPI, image files keep their own resolution."""
        return config.print_dpi if is_document(self.source_path) else self.result.detect_dpi

    def _tag(self, img: Image.Image, config: Config) -> Image.Image:
        # Record the resolution so the printer can print at actual size, and
        # a saved PNG opens at the right physical size.
        dpi = self.print_dpi(config)
        if img is self.result.image:
            img = img.copy()   # never tag (or later mutate) the preview itself
        img.info["dpi"] = (dpi, dpi)
        return img

    def _render_crop(self, config: Config) -> Image.Image:
        if is_document(self.source_path):
            base = rasterize_pdf_region(
                self.source_path, self.result.box,
                self.result.detect_dpi, config.print_dpi, page=self.page_index,
            )
            return _rotate_upright(base, self.result.orientation)
        # Image files were detected at native resolution, so the preview crop
        # already is print quality.
        return self.result.image

    def _render_page(self, config: Config) -> Image.Image:
        if is_document(self.source_path):
            return rasterize_pdf(self.source_path, config.print_dpi, page=self.page_index)
        return load_image(self.source_path)


def can_learn(lab: ProcessedLabel) -> bool:
    """Crop memory works per page: a single label (or document) on it, not one
    cell of an N-up sheet."""
    return lab.is_printable and lab.region_count == 1


def apply_memory(lab: ProcessedLabel, page: Image.Image, memory: learn.CropMemory,
                 config: Config, ink=None) -> bool:
    """If ``lab``'s layout has a learned crop, use it. ``page`` is the source
    page at the detection DPI. Returns True when a learned crop was applied."""
    if not can_learn(lab):
        return False
    r = lab.result
    dpi = r.detect_dpi
    if ink is None:
        ink = learn.ink_of(page)
    if lab.fingerprint is None:
        lab.fingerprint = learn.fingerprint(ink, dpi, r.barcode_boxes, r.barcode_types)
    hit = memory.find(lab.fingerprint)
    if hit is None:
        return False
    box, sample = hit.crop(dpi, page.size)
    margin = 0
    if not sample.whole and not sample.exact:
        # Follow this label's own content, then add the usual quiet zone.
        box = learn.Snapper(ink, dpi).snap(box)
        margin = int(round(dpi * config.crop_margin_inches))
    W, H = page.size
    final = (max(0, box[0] - margin), max(0, box[1] - margin),
             min(W, box[2] + margin), min(H, box[3] + margin))
    warnings, confidence = [], 0.97
    if learn.barcodes_inside(final, r.barcode_boxes) < sample.inside:
        confidence = 0.6
        warnings.append("The crop you taught PrintPal for this layout cut off a barcode "
                        "on this label -- check the preview.")
    try:
        lab.apply_box(box, page, margin_px=margin, method="learned",
                      confidence=confidence, warnings=warnings)
    except ValueError:
        return False
    lab.learned = hit.name
    return True


def process_file(path: str, config: Config,
                 progress: ProgressCallback | None = None,
                 memory: learn.CropMemory | None = None) -> list[ProcessedLabel]:
    """Detect every printable label in `path`.

    Returns one ProcessedLabel per page that carries content. If every page is
    blank, the blank pages are returned so the caller can explain why. With a
    ``memory``, pages whose layout has a learned crop use it.
    """
    total = 1
    capped = 1
    labels: list[ProcessedLabel] = []

    for i, total, img in iter_pages(path, dpi=config.detect_dpi, limit=MAX_PAGES):
        capped = min(total, MAX_PAGES)
        if progress:
            progress(f"Finding label{'' if capped == 1 else f' on page {i + 1}'}…",
                     i, capped)
        # Documents (PDF/XPS) are rendered at a known DPI; image files carry their
        # own (or a sensible default), so the physical-size regime and the inches
        # read-out stay honest instead of assuming the detection DPI.
        dpi = config.detect_dpi if is_document(path) else image_dpi(path, img)
        found = find_labels(img, dpi=dpi, margin_inches=config.crop_margin_inches,
                            split_nup=config.split_nup)
        page_labels = [ProcessedLabel(path, i, capped, result,
                                      region_index=r_idx, region_count=len(found))
                       for r_idx, result in enumerate(found)]
        if memory is not None and len(memory) and len(page_labels) == 1:
            apply_memory(page_labels[0], img, memory, config)
        labels.extend(page_labels)

    if total > MAX_PAGES:
        for lab in labels:
            lab.result.warnings.append(
                f"This file has {total} pages; only the first {MAX_PAGES} were scanned.")

    printable = [lab for lab in labels if lab.is_printable]
    return printable if printable else labels
