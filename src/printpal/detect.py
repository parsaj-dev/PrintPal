"""Barcode-anchored shipping label detection and cropping.

Finds barcodes on a rasterized page, grows from them to the full label using
axis-wise gap merging, rotates to upright, and verifies the output barcodes
still scan. No network, no LLM, pure local CV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import cv2
import numpy as np
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode, Decoded


@dataclass
class LabelResult:
    image: Image.Image
    confidence: float           # 0.0 to 1.0
    method: str
    barcodes_in: int            # count in the source
    barcodes_out: int           # count in the cropped output (recheck)
    box: tuple[int, int, int, int]  # (x0, y0, x1, y1) in source px, pre-rotation
    orientation: str = "UP"     # barcode orientation used for rotation
    detect_dpi: int = 200       # DPI the detection was run at
    warnings: list[str] = field(default_factory=list)


# -- internals ----------------------------------------------------------------

def _content_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Find contiguous True runs in a 1D boolean array. Returns (start, end) pairs."""
    runs: list[tuple[int, int]] = []
    start = None
    for i, val in enumerate(mask):
        if val and start is None:
            start = i
        elif not val and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _merge_runs_around(runs: list[tuple[int, int]], gap_px: int, lo: int, hi: int) -> tuple[int, int]:
    """Merge adjacent runs separated by gaps smaller than gap_px, then return
    the merged span that overlaps the anchor interval [lo, hi]."""
    if not runs:
        return lo, hi
    merged: list[list[int]] = [list(runs[0])]
    for a, b in runs[1:]:
        if a - merged[-1][1] < gap_px:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    for a, b in merged:
        if b > lo and a < hi:
            return min(a, lo), max(b, hi)
    return lo, hi


def _measure_gaps(runs: list[tuple[int, int]]) -> list[int]:
    """Return the list of gap sizes between consecutive runs."""
    gaps = []
    for i in range(1, len(runs)):
        gaps.append(runs[i][0] - runs[i - 1][1])
    return gaps


def _adaptive_gap_threshold(gaps: list[int], fallback_px: int) -> int:
    """Pick a gap merge threshold from the actual gap distribution on this page.

    Strategy: find the largest jump between sorted gap sizes. Gaps below that
    jump are intra-label spacing; gaps above are inter-section whitespace.
    Falls back to the hardcoded value when there aren't enough gaps to decide.
    """
    if len(gaps) < 3:
        return fallback_px
    s = sorted(gaps)
    best_jump = 0
    best_idx = -1
    for i in range(1, len(s)):
        jump = s[i] - s[i - 1]
        if jump > best_jump:
            best_jump = jump
            best_idx = i
    if best_idx < 1 or best_jump < fallback_px * 0.3:
        return fallback_px
    # threshold sits just above the largest intra-label gap
    return s[best_idx - 1] + max(1, best_jump // 4)


def _grow_to_block(ink: np.ndarray, seed: tuple[int, int, int, int], dpi: int) -> tuple[int, int, int, int]:
    """Grow barcode bounding box to the full label via two-pass gap merge."""
    H, W = ink.shape
    sx0, sy0, sx1, sy1 = seed
    ink_frac = 0.008

    v_fallback = int(dpi * 0.45)
    h_fallback = int(dpi * 0.30)

    # vertical pass inside the barcode's x-column
    strip = ink[:, sx0:sx1]
    row_has = strip.sum(axis=1) > (sx1 - sx0) * ink_frac
    v_runs = _content_runs(row_has)
    v_gaps = _measure_gaps(v_runs)
    v_gap = _adaptive_gap_threshold(v_gaps, v_fallback)
    y0, y1 = _merge_runs_around(v_runs, v_gap, sy0, sy1)

    # horizontal pass inside that vertical band
    band = ink[y0:y1, :]
    col_has = band.sum(axis=0) > (y1 - y0) * ink_frac
    h_runs = _content_runs(col_has)
    h_gaps = _measure_gaps(h_runs)
    h_gap = _adaptive_gap_threshold(h_gaps, h_fallback)
    x0, x1 = _merge_runs_around(h_runs, h_gap, sx0, sx1)

    return x0, y0, x1, y1


def _dominant_orientation(barcodes: Sequence[Decoded]) -> str:
    if not barcodes:
        return "UP"
    counts: dict[str, int] = {}
    for b in barcodes:
        o = str(b.orientation) if hasattr(b, "orientation") else "UP"
        counts[o] = counts.get(o, 0) + 1
    return max(counts, key=counts.get)


def _rotate_upright(img: Image.Image, orientation: str) -> Image.Image:
    table = {
        "UP": img,
        "DOWN": img.rotate(180, expand=True),
        "LEFT": img.rotate(270, expand=True),
        "RIGHT": img.rotate(90, expand=True),
    }
    return table.get(orientation, img)


def _find_blobs(ink: np.ndarray, dpi: int) -> list[tuple[int, int, int, int, int]]:
    """Find connected content regions via morphological closing.
    Returns list of (x0, y0, x1, y1, area) sorted by area descending."""
    close_px = max(8, int(dpi * 0.5))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (close_px, close_px))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        blobs.append((x, y, x + w, y + h, area))
    blobs.sort(key=lambda b: b[4], reverse=True)
    return blobs


def _blob_containing_point(blobs: list[tuple[int, int, int, int, int]],
                           cx: int, cy: int) -> tuple[int, int, int, int] | None:
    """Return the largest blob whose bounding box contains (cx, cy)."""
    for x0, y0, x1, y1, _ in blobs:
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return x0, y0, x1, y1
    return None


def _fallback_largest_blob(ink: np.ndarray, dpi: int) -> tuple[int, int, int, int] | None:
    """Find the largest connected content region as a last resort."""
    blobs = _find_blobs(ink, dpi)
    if not blobs:
        return None
    x0, y0, x1, y1, _ = blobs[0]
    return x0, y0, x1, y1


# -- public API ----------------------------------------------------------------

def find_label(img: Image.Image, dpi: int = 200) -> LabelResult:
    """Detect and crop a shipping label from a page image.

    Returns a LabelResult with the cropped, upright label and metadata about
    how confident we are it worked.
    """
    rgb = np.array(img)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ink = (gray < 200).astype(np.uint8)

    barcodes = zbar_decode(img)
    warnings: list[str] = []

    if barcodes:
        xs0 = [b.rect.left for b in barcodes]
        ys0 = [b.rect.top for b in barcodes]
        xs1 = [b.rect.left + b.rect.width for b in barcodes]
        ys1 = [b.rect.top + b.rect.height for b in barcodes]
        seed = (min(xs0), min(ys0), max(xs1), max(ys1))
        box = _grow_to_block(ink, seed, dpi)
        orientation = _dominant_orientation(barcodes)

        # the gap merge can be too conservative for labels with large internal
        # whitespace (address blocks separated from barcodes by big gaps).
        # Find the morphological blob that contains the barcode center and
        # use it if it's bigger than the gap-merge result.
        bc_cx = (seed[0] + seed[2]) // 2
        bc_cy = (seed[1] + seed[3]) // 2
        blobs = _find_blobs(ink, dpi)
        blob = _blob_containing_point(blobs, bc_cx, bc_cy)
        if blob is not None:
            bx0, by0, bx1, by1 = blob
            gx0, gy0, gx1, gy1 = box
            blob_area = (bx1 - bx0) * (by1 - by0)
            gap_area = (gx1 - gx0) * (gy1 - gy0)
            # use the blob if it's substantially larger but not the whole page
            # (if it's >90% of the page, the blob probably merged label + instructions)
            page_area = img.width * img.height
            if blob_area > gap_area * 1.3 and blob_area < page_area * 0.9:
                box = blob
            elif blob_area >= page_area * 0.9:
                # whole page is one blob -- the page IS the label
                box = blob

        confidence = 0.9
        method = "barcode-anchored"
    else:
        blob = _fallback_largest_blob(ink, dpi)
        if blob is None:
            return LabelResult(
                image=img, confidence=0.0, method="no-content",
                barcodes_in=0, barcodes_out=0,
                box=(0, 0, img.width, img.height),
                warnings=["No content detected on this page."],
            )
        box = blob
        orientation = "UP"
        confidence = 0.3
        method = "fallback-largest-blob"
        warnings.append("No barcodes found. Using largest content block as fallback.")

    # add quiet-zone margin
    margin = int(dpi * 0.06)
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(img.width, x1 + margin)
    y1 = min(img.height, y1 + margin)

    crop = img.crop((x0, y0, x1, y1))
    upright = _rotate_upright(crop, orientation)

    # recheck: do the barcodes still scan after cropping?
    recheck = zbar_decode(upright)
    barcodes_out = len(recheck)

    if barcodes and barcodes_out == 0:
        confidence = max(confidence - 0.5, 0.1)
        warnings.append("Barcodes did not scan in the cropped output.")
    elif barcodes and barcodes_out < len(barcodes):
        confidence = max(confidence - 0.2, 0.3)
        warnings.append(f"Only {barcodes_out} of {len(barcodes)} barcodes survived cropping.")

    return LabelResult(
        image=upright,
        confidence=confidence,
        method=method,
        barcodes_in=len(barcodes),
        barcodes_out=barcodes_out,
        box=(x0, y0, x1, y1),
        orientation=orientation,
        detect_dpi=dpi,
        warnings=warnings,
    )


def crop_at_dpi(img_hq: Image.Image, result: LabelResult, output_dpi: int) -> Image.Image:
    """Re-crop a higher-DPI render using the box found at detection DPI.

    Scales the detection box coordinates from detect_dpi to output_dpi,
    crops, and rotates. Use this to get print-quality output from a
    fast low-DPI detection pass.
    """
    scale = output_dpi / result.detect_dpi
    x0, y0, x1, y1 = result.box
    hx0 = max(0, int(x0 * scale))
    hy0 = max(0, int(y0 * scale))
    hx1 = min(img_hq.width, int(x1 * scale))
    hy1 = min(img_hq.height, int(y1 * scale))

    crop = img_hq.crop((hx0, hy0, hx1, hy1))
    return _rotate_upright(crop, result.orientation)
