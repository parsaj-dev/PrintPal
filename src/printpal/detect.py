"""Shipping-label detection and cropping.

The job: take a rasterized page (which may be a bare 4x6 label, or a Letter/A4
sheet that carries a label plus instructions, a packing slip, or legal text)
and return the smallest upright crop that contains the whole label -- every
barcode *and* every scrap of address text -- and nothing else.

Two regimes, chosen by the page's physical size:

* **Label media** (short side <= ~6.5 in): the page *is* the label. Trim the
  outer white margin and print it. Fast and near-foolproof.

* **Document media** (Letter, A4, ...): the label is a dense block somewhere on
  the sheet. We find it by closing the ink into solid regions and picking the
  connected component that carries the barcodes, then union in any barcode that
  landed just outside. Closing with a kernel sized to bridge *intra-label* gaps
  (address line spacing, the gap between address block and barcode) but not the
  larger whitespace that separates the label from instructions is what keeps the
  address text attached to the barcodes -- the thing the old gap-merge missed.

No network, no LLM, pure local OpenCV + zbar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode

# Below this short-side length (inches) we treat the whole page as the label.
# 4x6, 4x8, 6x4 labels have a short side <= 6"; Letter/A4 short side is >= 8.2".
LABEL_MEDIA_MAX_SHORT_IN = 6.5

# Ink threshold: pixels darker than this (0-255 grey) count as content.
_INK_THRESHOLD = 200

# Map a barcode's reported reading orientation to the PIL transpose that brings
# the crop upright. Verified empirically against real rotated FedEx/UPS labels:
# a "RIGHT" barcode becomes upright under a 90-degree counter-clockwise turn.
_ROTATION_FOR = {
    "UP": None,
    "DOWN": Image.ROTATE_180,
    "LEFT": Image.ROTATE_270,
    "RIGHT": Image.ROTATE_90,
}


# Page classification, used for the label count, smart routing and history.
KIND_LABEL = "label"          # a shipping label (barcode-backed, or on label media)
KIND_DOCUMENT = "document"    # a document-media page with no shipping barcode: a
                              # packing slip, instructions sheet, invoice, ...
KIND_BLANK = "blank"          # nothing to print


@dataclass
class LabelResult:
    image: Image.Image                 # cropped, upright label at detect DPI (preview)
    confidence: float                  # 0.0 to 1.0
    method: str                        # how the box was found
    barcodes_in: int                   # barcodes decoded on the source page
    barcodes_out: int                  # barcodes still decodable after cropping
    box: tuple[int, int, int, int]     # (x0, y0, x1, y1) in source px, pre-rotation
    orientation: str = "UP"            # barcode orientation used for rotation
    detect_dpi: int = 200              # DPI the page was rasterized at
    is_full_page: bool = False         # True when the whole media is the label
    kind: str = KIND_LABEL             # KIND_LABEL / KIND_DOCUMENT / KIND_BLANK
    barcode_data: list[str] = field(default_factory=list)  # decoded payloads
    warnings: list[str] = field(default_factory=list)

    @property
    def is_label(self) -> bool:
        return self.kind == KIND_LABEL


# -- low level helpers --------------------------------------------------------

def _ink_mask(gray: np.ndarray) -> np.ndarray:
    return (gray < _INK_THRESHOLD).astype(np.uint8)


def _content_bbox(ink: np.ndarray) -> tuple[int, int, int, int] | None:
    """Tight bounding box of all ink, or None if the page is blank."""
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _rect_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def _union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _decode(img: Image.Image, gray: np.ndarray | None = None):
    """Decode 1D + 2D barcodes. Feed zbar a greyscale image -- it is what zbar
    works on internally, and it is measurably faster than handing it RGB."""
    src = Image.fromarray(gray) if gray is not None else img
    return zbar_decode(src)


def _barcode_rects(barcodes) -> list[tuple[int, int, int, int]]:
    rects = []
    for b in barcodes:
        r = b.rect
        rects.append((r.left, r.top, r.left + r.width, r.top + r.height))
    return rects


def _barcode_payloads(barcodes) -> list[str]:
    """Decoded barcode text (deduped, order preserved). Bytes -> UTF-8/latin-1."""
    out: list[str] = []
    for b in barcodes:
        data = getattr(b, "data", None)
        if data is None:
            continue
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8")
            except UnicodeDecodeError:
                data = data.decode("latin-1", "replace")
        data = str(data).strip()
        if data and data not in out:
            out.append(data)
    return out


def _barcode_center(b) -> tuple[float, float]:
    r = b.rect
    return (r.left + r.width / 2.0, r.top + r.height / 2.0)


def _dominant_orientation(barcodes) -> str:
    """Most common barcode orientation, weighted by barcode area so a big
    shipping barcode outvotes a tiny 2D symbol that zbar read sideways."""
    if not barcodes:
        return "UP"
    weight: dict[str, float] = {}
    for b in barcodes:
        o = str(getattr(b, "orientation", "UP") or "UP")
        area = max(1, b.rect.width * b.rect.height)
        weight[o] = weight.get(o, 0.0) + area
    return max(weight, key=weight.get)


def _rotate_upright(img: Image.Image, orientation: str) -> Image.Image:
    op = _ROTATION_FOR.get(orientation)
    if op is None:
        return img
    return img.transpose(op)


def _components(ink: np.ndarray, dpi: int) -> list[tuple[int, int, int, int, int]]:
    """Close the ink into solid regions and return their bounding boxes as
    (x0, y0, x1, y1, area), largest area first.

    The closing kernel (~0.35 in) is the crux: big enough to fuse an address
    block, its logo and its barcodes into one region, small enough to leave the
    label separate from instruction text set off by a wider margin or a cut line.
    """
    close = max(6, int(round(dpi * 0.35)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close, close))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats(closed, connectivity=8)
    comps = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        comps.append((int(x), int(y), int(x + w), int(y + h), int(area)))
    comps.sort(key=lambda c: c[4], reverse=True)
    return comps


def _grow_aligned(box: tuple[int, int, int, int],
                  comps: list[tuple[int, int, int, int, int]], dpi: int
                  ) -> tuple[int, int, int, int]:
    """Grow ``box`` to swallow components that are part of the same label.

    A component joins the label when it is horizontally aligned with the current
    box (a meaningful x-overlap -- i.e. it sits in the same column) and separated
    by only a small vertical gap. That reattaches the carrier band and address
    block sitting above a barcode without reaching across the wider whitespace or
    divider that sets instructions apart. Runs to a fixed point so a chain of
    stacked blocks all come in.
    """
    gap_max = int(round(dpi * 0.7))          # bridge <= 0.7" of vertical whitespace
    changed = True
    while changed:
        changed = False
        bx0, by0, bx1, by1 = box
        for cx0, cy0, cx1, cy1, _area in comps:
            if cx0 >= bx0 and cy0 >= by0 and cx1 <= bx1 and cy1 <= by1:
                continue  # already inside
            overlap_x = min(bx1, cx1) - max(bx0, cx0)
            if overlap_x <= 0:
                continue
            if overlap_x < 0.35 * min(bx1 - bx0, cx1 - cx0):
                continue  # only clips the edge -- a neighbouring column, not ours
            if cy0 >= by1:
                gap = cy0 - by1
            elif cy1 <= by0:
                gap = by0 - cy1
            else:
                gap = 0
            if gap > gap_max:
                continue
            grown = _union(box, (cx0, cy0, cx1, cy1))
            if grown != box:
                box = grown
                bx0, by0, bx1, by1 = box
                changed = True
    return box


def _label_block(ink: np.ndarray, barcode_rects, dpi: int
                 ) -> tuple[tuple[int, int, int, int], str]:
    """Find the label's bounding box on a document-sized page."""
    comps = _components(ink, dpi)
    if not comps:
        H, W = ink.shape
        return (0, 0, W, H), "whole-page"

    if not barcode_rects:
        # No barcode anchor: fall back to the largest content region.
        x0, y0, x1, y1, _ = comps[0]
        return (x0, y0, x1, y1), "largest-block"

    # Pick the component that overlaps the barcodes the most.
    def bc_overlap(c):
        return sum(_rect_overlap(c[:4], r) for r in barcode_rects)

    best = max(comps, key=bc_overlap)
    box = best[:4]

    # Reattach the address/carrier blocks stacked above (or below) the barcode
    # that the morphological close left as separate components.
    box = _grow_aligned(box, comps, dpi)

    # Any barcode that is mostly outside the chosen block belongs to the label
    # too (e.g. a tracking barcode set apart by a divider). Union its component.
    for r in barcode_rects:
        r_area = (r[2] - r[0]) * (r[3] - r[1])
        if _rect_overlap(box, r) < 0.5 * r_area:
            for c in comps:
                if _rect_overlap(c[:4], r) > 0:
                    box = _union(box, _grow_aligned(c[:4], comps, dpi))
                    break
            else:
                box = _union(box, r)
    return box, "barcode-block"


# -- public API ---------------------------------------------------------------

def find_label(img: Image.Image, dpi: int = 200,
               margin_inches: float = 0.08) -> LabelResult:
    """Detect and crop the shipping label on a rasterized page.

    `img` is one page rendered at `dpi`. Returns a LabelResult whose `image` is
    the upright, cropped label (at detect DPI, for preview) and whose `box`
    /`orientation` can be replayed against a higher-DPI render for printing.
    """
    rgb = np.asarray(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ink = _ink_mask(gray)
    H, W = ink.shape
    page_area = W * H

    short_in = min(W, H) / dpi
    is_label_media = short_in <= LABEL_MEDIA_MAX_SHORT_IN

    barcodes = _decode(img, gray)
    if not barcodes and not is_label_media:
        # Dense codes on a big sheet sometimes need more resolution. One retry at
        # 1.5x by upscaling the grey plane is cheap next to re-rasterizing.
        big = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        retry = zbar_decode(Image.fromarray(big))
        if retry:
            class _Scaled:  # shim so downstream sees source-resolution rects
                def __init__(s, d):
                    s.rect = type("R", (), {
                        "left": int(d.rect.left / 1.5), "top": int(d.rect.top / 1.5),
                        "width": int(d.rect.width / 1.5), "height": int(d.rect.height / 1.5)})
                    s.orientation = getattr(d, "orientation", "UP")
                    s.type = d.type
                    s.data = getattr(d, "data", b"")
            barcodes = [_Scaled(d) for d in retry]

    warnings: list[str] = []
    brects = _barcode_rects(barcodes)

    if _content_bbox(ink) is None:
        return LabelResult(
            image=img, confidence=0.0, method="no-content",
            barcodes_in=0, barcodes_out=0, box=(0, 0, W, H),
            detect_dpi=dpi, kind=KIND_BLANK,
            warnings=["This page looks blank -- no content to print."],
        )

    if is_label_media:
        kind = KIND_LABEL
        is_full_page = True
        if barcodes:
            # Trim the outer white margin so the printer fills the media, but keep
            # the whole label -- content bbox already hugs every mark on the page.
            box = _content_bbox(ink) or (0, 0, W, H)
            method = "label-media"
            confidence = 0.92
        else:
            # No barcode read: could be a packing slip, or a label whose code
            # failed to scan. Don't gamble on a tight crop -- keep the whole page.
            box = (0, 0, W, H)
            method = "label-media-no-barcode"
            confidence = 0.4
            warnings.append("No barcode found on this label -- printing the whole page.")
    else:
        is_full_page = False
        kind = KIND_LABEL if barcodes else KIND_DOCUMENT
        box, method = _label_block(ink, brects, dpi)
        if barcodes:
            confidence = 0.9
        else:
            confidence = 0.3
            warnings.append("No barcode found. Cropped to the largest block of content -- "
                            "please check the preview.")
        # If block detection basically returned the whole sheet, the crop isn't
        # doing its job; flag it rather than silently printing instructions.
        bx0, by0, bx1, by1 = box
        if (bx1 - bx0) * (by1 - by0) >= page_area * 0.9 and barcodes:
            confidence = min(confidence, 0.55)
            warnings.append("The label fills most of the page -- the crop may include "
                            "extra text. Check the preview.")

    orientation = _dominant_orientation(barcodes)

    # Quiet-zone margin so a barcode is never clipped flush to the edge.
    margin = int(round(dpi * max(0.0, margin_inches)))
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - margin); y0 = max(0, y0 - margin)
    x1 = min(W, x1 + margin); y1 = min(H, y1 + margin)
    box = (x0, y0, x1, y1)

    crop = img.crop(box)
    upright = _rotate_upright(crop, orientation)

    # Quality gate: do the barcodes still decode after cropping + rotating?
    recheck = _decode(upright)
    barcodes_out = len(recheck)
    if barcodes:
        if barcodes_out == 0:
            confidence = max(0.1, confidence - 0.45)
            warnings.append("Barcodes did not re-scan in the crop -- verify before printing.")
        elif barcodes_out < len(barcodes):
            confidence = max(0.35, confidence - 0.15)

    return LabelResult(
        image=upright,
        confidence=round(confidence, 2),
        method=method,
        barcodes_in=len(barcodes),
        barcodes_out=barcodes_out,
        box=box,
        orientation=orientation,
        detect_dpi=dpi,
        is_full_page=is_full_page,
        kind=kind,
        barcode_data=_barcode_payloads(barcodes),
        warnings=warnings,
    )


# -- N-up: multiple labels on one page ----------------------------------------

def _cluster_1d(values: list[float], gap: float) -> list[list[float]]:
    """Group sorted values into clusters, starting a new one on a gap > `gap`."""
    sv = sorted(values)
    clusters = [[sv[0]]]
    for v in sv[1:]:
        if v - clusters[-1][-1] > gap:
            clusters.append([v])
        else:
            clusters[-1].append(v)
    return clusters


def _widest_zero_mid(profile: np.ndarray, lo: int, hi: int, min_run: int) -> int | None:
    """Midpoint of the widest all-zero run in profile[lo:hi], or None if the
    widest is shorter than `min_run`. This is the gutter between two labels."""
    lo = max(0, lo)
    hi = min(len(profile), hi)
    best_mid, best_len, run_start = None, 0, None
    for i in range(lo, hi):
        if profile[i] == 0:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and i - run_start > best_len:
                best_len, best_mid = i - run_start, (run_start + i) // 2
            run_start = None
    if run_start is not None and hi - run_start > best_len:
        best_len, best_mid = hi - run_start, (run_start + hi) // 2
    return best_mid if best_len >= min_run else None


def _axis_bounds(centers: list[float], profile: np.ndarray, dpi: int, page_len: int,
                 cluster_gap_in: float = 1.6, min_gutter_in: float = 0.1) -> list[int]:
    """Cut positions along one axis: cluster the barcode centres, then split only
    at a genuine whitespace gutter *between* adjacent clusters. Returns
    ``[0, page_len]`` (no split) unless real between-label gutters are found -- so
    a single label's internal whitespace never causes a cut."""
    if len(centers) < 2:
        return [0, page_len]
    clusters = _cluster_1d(centers, cluster_gap_in * dpi)
    if len(clusters) < 2:
        return [0, page_len]
    min_gutter = max(3, int(round(min_gutter_in * dpi)))
    bounds = [0]
    for k in range(len(clusters) - 1):
        lo, hi = int(max(clusters[k])), int(min(clusters[k + 1]))
        mid = _widest_zero_mid(profile, lo, hi, min_gutter)
        if mid is not None:
            bounds.append(mid)
    bounds.append(page_len)
    return sorted(set(bounds))


def find_labels(img: Image.Image, dpi: int = 200, margin_inches: float = 0.08,
                split_nup: bool = True) -> list[LabelResult]:
    """Detect every label on a page, splitting N-up sheets into one per label.

    Amazon/Etsy-style pages that carry 2 or 4 identical labels in a grid are
    split into individual 4x6 crops: the barcodes are clustered into a grid, the
    page is cut at the whitespace gutters *between* those clusters, and each cell
    is run through the normal single-label detector. Ordinary pages (and pages
    whose barcodes don't form a clean grid) return a single result identical to
    `find_label`.
    """
    if not split_nup:
        return [find_label(img, dpi, margin_inches)]

    rgb = np.asarray(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ink = _ink_mask(gray)
    H, W = ink.shape

    barcodes = _decode(img, gray)
    if len(barcodes) < 2:
        return [find_label(img, dpi, margin_inches)]

    centers = [_barcode_center(b) for b in barcodes]
    xb = _axis_bounds([c[0] for c in centers], ink.sum(axis=0), dpi, W)
    yb = _axis_bounds([c[1] for c in centers], ink.sum(axis=1), dpi, H)
    if len(xb) <= 2 and len(yb) <= 2:
        return [find_label(img, dpi, margin_inches)]  # no between-label gutter -> single

    results: list[LabelResult] = []
    for j in range(len(yb) - 1):
        for i in range(len(xb) - 1):
            cx0, cy0, cx1, cy1 = xb[i], yb[j], xb[i + 1], yb[j + 1]
            if not any(cx0 <= cx < cx1 and cy0 <= cy < cy1 for cx, cy in centers):
                continue  # a cell with no barcode is not a label
            cell = img.crop((cx0, cy0, cx1, cy1))
            r = find_label(cell, dpi, margin_inches)
            if r.kind == KIND_BLANK:
                continue
            bx0, by0, bx1, by1 = r.box
            r.box = (bx0 + cx0, by0 + cy0, bx1 + cx0, by1 + cy0)  # back to page coords
            results.append(r)

    if sum(1 for r in results if r.is_label) >= 2:
        return results
    return [find_label(img, dpi, margin_inches)]


def scale_box(box: tuple[int, int, int, int], from_dpi: int, to_dpi: int,
              clip_w: int | None = None, clip_h: int | None = None) -> tuple[int, int, int, int]:
    """Scale a detection-DPI box to another DPI, optionally clamped to bounds."""
    s = to_dpi / from_dpi
    x0, y0, x1, y1 = (int(round(v * s)) for v in box)
    x0 = max(0, x0); y0 = max(0, y0)
    if clip_w is not None:
        x1 = min(clip_w, x1)
    if clip_h is not None:
        y1 = min(clip_h, y1)
    return x0, y0, x1, y1
