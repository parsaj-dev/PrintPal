"""Crop memory: learn the right crop for a label layout from the user's fixes.

When the automatic crop is wrong and the user fixes it in Adjust crop, PrintPal
remembers the fix for that *layout* (not that one file), so the next label made
from the same template comes out right with no clicks.

Three pieces, all pure (NumPy/OpenCV, no UI), so they are unit-tested headless:

* **Fingerprint** -- what makes two pages "the same template": the page size,
  and where each barcode sits (symbology, centre and size, in inches). A
  carrier's template puts its barcodes in the same place on every label, while
  the address, tracking number and item list change. A coarse ink-density grid
  backs that up (and is the whole fingerprint for a page with no barcode).

* **Snapping** -- the user's box is never exact, so it is not stored as drawn.
  It is snapped to the content it encloses: every block of ink that is mostly
  inside the box is kept whole (a text line the box clipped comes back), and
  every block mostly outside is dropped (a stray edge of the instructions goes).
  The same snap runs again on each new label, so the learned crop follows that
  label's own content (a longer address, a shifted logo) instead of replaying
  pixels.

* **Memory** -- a small JSON file of templates, each with the last few snapped
  corrections. The crop used is the per-edge median of those, so one sloppy fix
  doesn't undo good ones; a fix that disagrees with the others by a wide margin
  is a change of mind and replaces them.

Coordinates in the store are inches in the template's own page frame, so they
survive a change of detection DPI. Applying a template translates them by the
offset between the template's barcodes and the new page's.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from typing import TYPE_CHECKING

from printpal.config import _CONFIG_DIR

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

# OpenCV/NumPy are imported inside the functions that use them: the window
# creates the memory at startup, and loading the store must stay cheap.

Box = tuple[int, int, int, int]

FORMAT_VERSION = 1

# -- matching ------------------------------------------------------------------
PAGE_TOL_IN = 0.25          # same template = same page size, within this
BARCODE_TOL_IN = 0.35       # a barcode matches if its centre lands this close
MAX_SHIFT_IN = 1.5          # how far a whole template may sit from where we saw it
MIN_BARCODE_SCORE = 0.6     # share of barcodes that must line up
MIN_GRID_CORR = 0.4         # sanity check on the overall ink layout ...
MIN_GRID_CORR_NO_BARCODE = 0.9   # ... and the whole test when there is no barcode
GRID_COLS = 12

# -- snapping ------------------------------------------------------------------
SNAP_CLOSE_IN = 0.06        # fuse letters into words/lines, not paragraphs
SNAP_MIN_INSIDE = 0.5       # a block is "in" when at least half of it is
SNAP_MAX_GROW_IN = 0.4      # never let a block drag an edge further than this
SNAP_MIN_BLOCK_IN = 0.02    # ignore specks smaller than this

# -- storage -------------------------------------------------------------------
MAX_SAMPLES = 5             # corrections kept per template
CHANGE_OF_MIND_IN = 0.5     # an edge this far from the others starts over
MAX_TEMPLATES = 300


# -- ink ------------------------------------------------------------------------

def ink_of(page: "Image.Image") -> "np.ndarray":
    """0/1 ink mask of a page, the same threshold detection uses."""
    from printpal.detect import _ink_mask, _to_gray
    return _ink_mask(_to_gray(page))


# -- fingerprint ----------------------------------------------------------------

@dataclass
class Fingerprint:
    page: tuple[float, float]                                   # (w, h) inches
    barcodes: list[tuple[str, float, float, float, float]]      # type, cx, cy, w, h (in)
    grid: list[float]                                           # ink density, row-major
    grid_shape: tuple[int, int]                                 # (cols, rows)

    def to_json(self) -> dict:
        return {"page": list(self.page), "barcodes": [list(b) for b in self.barcodes],
                "grid": self.grid, "grid_shape": list(self.grid_shape)}

    @classmethod
    def from_json(cls, d: dict) -> "Fingerprint":
        return cls(page=(float(d["page"][0]), float(d["page"][1])),
                   barcodes=[(str(b[0]), *(float(v) for v in b[1:5])) for b in d["barcodes"]],
                   grid=[float(v) for v in d["grid"]],
                   grid_shape=(int(d["grid_shape"][0]), int(d["grid_shape"][1])))


def fingerprint(ink: "np.ndarray", dpi: int, barcode_boxes, barcode_types) -> Fingerprint:
    """Layout fingerprint of a page from its ink mask and decoded barcodes
    (``LabelResult.barcode_boxes`` / ``barcode_types``, page px at ``dpi``)."""
    import cv2
    import numpy as np
    H, W = ink.shape
    cols = GRID_COLS
    rows = max(1, int(round(cols * H / max(1, W))))
    grid = cv2.resize(ink.astype(np.float32), (cols, rows), interpolation=cv2.INTER_AREA)
    bars = []
    for i, ((x0, y0, x1, y1), _o) in enumerate(barcode_boxes):
        kind = barcode_types[i] if i < len(barcode_types) else ""
        bars.append((kind, round((x0 + x1) / 2 / dpi, 3), round((y0 + y1) / 2 / dpi, 3),
                     round((x1 - x0) / dpi, 3), round((y1 - y0) / dpi, 3)))
    return Fingerprint(page=(round(W / dpi, 2), round(H / dpi, 2)), barcodes=bars,
                       grid=[round(float(v), 3) for v in grid.ravel()],
                       grid_shape=(cols, rows))


def _grid_corr(a: Fingerprint, b: Fingerprint) -> float | None:
    if a.grid_shape != b.grid_shape or not a.grid:
        return None
    import numpy as np
    x, y = np.asarray(a.grid), np.asarray(b.grid)
    if x.std() < 1e-6 or y.std() < 1e-6:
        return 1.0 if np.allclose(x, y, atol=0.02) else 0.0
    return float(np.corrcoef(x, y)[0, 1])


def match(template: Fingerprint, page: Fingerprint) -> tuple[float, float, float] | None:
    """How well ``page`` fits ``template``: (score, dx, dy) with the offset in
    inches that carries template coordinates onto the page, or None for a
    different layout."""
    if (abs(template.page[0] - page.page[0]) > PAGE_TOL_IN
            or abs(template.page[1] - page.page[1]) > PAGE_TOL_IN):
        return None
    corr = _grid_corr(template, page)
    tb, pb = template.barcodes, page.barcodes
    if not tb and not pb:
        if corr is None or corr < MIN_GRID_CORR_NO_BARCODE:
            return None
        return corr, 0.0, 0.0
    if not tb or not pb:
        return None   # one has a barcode, the other doesn't: not the same template

    # Each pairing of a template barcode with a same-type page barcode proposes
    # an offset; keep the one that lines up the most barcodes.
    hypotheses = {(0.0, 0.0)}
    for t in tb:
        for p in pb:
            if t[0] == p[0]:
                hypotheses.add((p[1] - t[1], p[2] - t[2]))
    best = None
    for dx, dy in hypotheses:
        if abs(dx) > MAX_SHIFT_IN or abs(dy) > MAX_SHIFT_IN:
            continue
        used: set[int] = set()
        deltas = []
        for t in tb:
            near = None
            for j, p in enumerate(pb):
                if j in used or p[0] != t[0]:
                    continue
                d = ((t[1] + dx - p[1]) ** 2 + (t[2] + dy - p[2]) ** 2) ** 0.5
                if d <= BARCODE_TOL_IN and (near is None or d < near[0]):
                    near = (d, j, p[1] - t[1], p[2] - t[2])
            if near is not None:
                used.add(near[1])
                deltas.append(near)
        if not deltas:
            continue
        score = len(deltas) / max(len(tb), len(pb))
        residual = sum(d[0] for d in deltas) / len(deltas)
        mdx = sum(d[2] for d in deltas) / len(deltas)
        mdy = sum(d[3] for d in deltas) / len(deltas)
        cand = (score, -residual, mdx, mdy)
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is None or best[0] < MIN_BARCODE_SCORE:
        return None
    if corr is not None and corr < MIN_GRID_CORR:
        return None
    return best[0] + 0.1 * (corr or 0.0), best[2], best[3]


# -- snapping -------------------------------------------------------------------

class Snapper:
    """Snaps a rough box to the content it encloses. Build once per page (it
    finds the page's blocks of ink), then ``snap`` as often as you like."""

    def __init__(self, ink: "np.ndarray", dpi: int):
        import cv2
        import numpy as np
        self.dpi = dpi
        self.H, self.W = ink.shape
        k = max(1, int(round(dpi * SNAP_CLOSE_IN / 2))) * 2 + 1   # odd: no 1 px shift
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)
        _n, _lab, stats, _c = cv2.connectedComponentsWithStats(closed, connectivity=8)
        stats = stats[1:]
        speck = (dpi * SNAP_MIN_BLOCK_IN) ** 2
        keep = (stats[:, 2] * stats[:, 3]) >= speck
        s = stats[keep]
        # (x0, y0, x1, y1) of every block.
        self.blocks = np.stack([s[:, 0], s[:, 1], s[:, 0] + s[:, 2], s[:, 1] + s[:, 3]],
                               axis=1).astype(np.int64) if len(s) else np.zeros((0, 4), np.int64)

    def snap(self, box: Box) -> Box:
        """The union of the blocks mostly inside ``box`` (no margin). A block
        that would pull an edge out by more than ``SNAP_MAX_GROW_IN`` is left
        out, so a box drawn next to a big block never swallows it. Returns
        ``box`` (clamped) when it encloses no content."""
        import numpy as np
        x0, y0, x1, y1 = (int(v) for v in box)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.W, x1), min(self.H, y1)
        if x1 <= x0 or y1 <= y0 or not len(self.blocks):
            return (x0, y0, x1, y1)
        b = self.blocks
        ix = np.clip(np.minimum(b[:, 2], x1) - np.maximum(b[:, 0], x0), 0, None)
        iy = np.clip(np.minimum(b[:, 3], y1) - np.maximum(b[:, 1], y0), 0, None)
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        grow = self.dpi * SNAP_MAX_GROW_IN
        keep = ((ix * iy) >= SNAP_MIN_INSIDE * area) & (ix * iy > 0)
        keep &= (b[:, 0] >= x0 - grow) & (b[:, 1] >= y0 - grow)
        keep &= (b[:, 2] <= x1 + grow) & (b[:, 3] <= y1 + grow)
        if not keep.any():
            return (x0, y0, x1, y1)
        k = b[keep]
        return (int(k[:, 0].min()), int(k[:, 1].min()), int(k[:, 2].max()), int(k[:, 3].max()))


def barcodes_inside(box: Box, barcode_boxes) -> int:
    """How many barcodes sit wholly inside ``box`` (i.e. survive the crop)."""
    return sum(1 for (x0, y0, x1, y1), _o in barcode_boxes
               if x0 >= box[0] and y0 >= box[1] and x1 <= box[2] and y1 <= box[3])


# -- naming ---------------------------------------------------------------------

_PAPERS = (("Letter", 8.5, 11.0), ("Legal", 8.5, 14.0), ("A4", 8.27, 11.69),
           ("4×6", 4.0, 6.0), ("4×8", 4.0, 8.0))


def paper_name(page: tuple[float, float]) -> str:
    w, h = sorted(page)
    for name, pw, ph in _PAPERS:
        if abs(w - pw) <= PAGE_TOL_IN and abs(h - ph) <= PAGE_TOL_IN:
            return name
    return f"{page[0]:.1f}″ × {page[1]:.1f}″"


def describe(fp: Fingerprint, carrier: str | None) -> str:
    """Plain-words name for a layout, e.g. "FedEx labels on Letter paper"."""
    paper = paper_name(fp.page)
    known = bool(carrier) and carrier != "Unknown"
    if paper in ("4×6", "4×8"):
        return f"{paper} {carrier} labels" if known and fp.barcodes else f"{paper} labels like this"
    on = f"on {paper} paper" if paper in ("Letter", "Legal", "A4") else f"({paper})"
    if not fp.barcodes:
        return f"pages like this {on}"
    return f"{carrier} labels {on}" if known else f"labels like this {on}"


# -- memory ---------------------------------------------------------------------

@dataclass
class Sample:
    """One correction, in inches in the template's page frame (no margin).
    ``exact``: a box drawn with snapping off -- used as is, no snap, no margin.
    ``whole``: the whole page. ``inside``: barcodes the crop kept."""
    box: tuple[float, float, float, float]
    exact: bool = False
    whole: bool = False
    inside: int = 0

    def to_json(self) -> dict:
        return {"box": [round(v, 3) for v in self.box], "exact": self.exact,
                "whole": self.whole, "inside": self.inside}

    @classmethod
    def from_json(cls, d: dict) -> "Sample":
        return cls(box=tuple(float(v) for v in d["box"]), exact=bool(d.get("exact")),
                   whole=bool(d.get("whole")), inside=int(d.get("inside", 0)))


@dataclass
class Template:
    id: str
    name: str
    fingerprint: Fingerprint
    samples: list[Sample]
    created: float
    updated: float

    def crop(self) -> Sample:
        """The crop to use: per-edge median of the kept corrections."""
        last = self.samples[-1]
        same = [s for s in self.samples if s.exact == last.exact and s.whole == last.whole]
        edges = [statistics.median(s.box[i] for s in same) for i in range(4)]
        return Sample(box=tuple(edges), exact=last.exact, whole=last.whole,
                      inside=min(s.inside for s in same))

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "fingerprint": self.fingerprint.to_json(),
                "samples": [s.to_json() for s in self.samples],
                "created": self.created, "updated": self.updated}

    @classmethod
    def from_json(cls, d: dict) -> "Template":
        return cls(id=str(d["id"]), name=str(d.get("name", "")),
                   fingerprint=Fingerprint.from_json(d["fingerprint"]),
                   samples=[Sample.from_json(s) for s in d["samples"]],
                   created=float(d.get("created", 0)), updated=float(d.get("updated", 0)))


@dataclass
class Hit:
    """A template that matched a page, and the crop it gives there."""
    template: Template
    dx: float           # inches: template frame -> this page
    dy: float

    @property
    def name(self) -> str:
        return self.template.name

    def crop(self, dpi: int, page_size: tuple[int, int]) -> tuple[Box, Sample]:
        """The learned crop on this page in px at ``dpi`` (clamped, no margin)."""
        s = self.template.crop()
        W, H = page_size
        if s.whole:
            return (0, 0, W, H), s
        x0, y0, x1, y1 = s.box
        box = (int(round((x0 + self.dx) * dpi)), int(round((y0 + self.dy) * dpi)),
               int(round((x1 + self.dx) * dpi)), int(round((y1 + self.dy) * dpi)))
        return (max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3])), s


class CropMemory:
    """The learned templates, kept in ``crop_memory.json`` next to the config.

    Thread-safe: detection workers call ``find`` while the UI thread may
    ``learn``. A missing or unreadable file is just an empty memory."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _CONFIG_DIR / "crop_memory.json"
        self._lock = threading.Lock()
        self._templates: list[Template] = self._load()

    def __len__(self) -> int:
        with self._lock:
            return len(self._templates)

    def _load(self) -> list[Template]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
            return []
        out = []
        for d in data.get("templates", []):
            try:
                t = Template.from_json(d)
            except (KeyError, TypeError, ValueError, IndexError):
                continue   # skip a damaged entry, keep the rest
            if t.samples:
                out.append(t)
        return out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": FORMAT_VERSION, "templates": [t.to_json() for t in self._templates]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    def _best(self, fp: Fingerprint) -> tuple[Template, float, float] | None:
        best = None
        for t in self._templates:
            m = match(t.fingerprint, fp)
            if m is not None and (best is None or m[0] > best[0]):
                best = (m[0], t, m[1], m[2])
        return None if best is None else best[1:]

    def find(self, fp: Fingerprint) -> Hit | None:
        with self._lock:
            b = self._best(fp)
        return None if b is None else Hit(*b)

    def learn(self, fp: Fingerprint, box_px: Box, dpi: int, *, name: str,
              exact: bool = False, whole: bool = False, inside: int = 0) -> Template:
        """Remember ``box_px`` (page px at ``dpi``, no margin) as the crop for
        ``fp``'s layout. Refines a matching template, or starts a new one."""
        now = time.time()
        with self._lock:
            b = self._best(fp)
            if b is None:
                t = Template(id=uuid.uuid4().hex[:12], name=name, fingerprint=fp,
                             samples=[], created=now, updated=now)
                self._templates.append(t)
                dx = dy = 0.0
            else:
                t, dx, dy = b
            box_in = (box_px[0] / dpi - dx, box_px[1] / dpi - dy,
                      box_px[2] / dpi - dx, box_px[3] / dpi - dy)
            sample = Sample(box=box_in, exact=exact, whole=whole, inside=inside)
            if t.samples:
                cur = t.crop()
                changed = (cur.exact != exact or cur.whole != whole
                           or max(abs(a - c) for a, c in zip(box_in, cur.box)) > CHANGE_OF_MIND_IN)
                if changed:
                    t.samples = []
            t.samples = (t.samples + [sample])[-MAX_SAMPLES:]
            t.name = name or t.name
            t.updated = now
            if len(self._templates) > MAX_TEMPLATES:
                self._templates.sort(key=lambda x: x.updated)
                del self._templates[:len(self._templates) - MAX_TEMPLATES]
            self._save()
            return t

    def forget(self, fp: Fingerprint) -> Template | None:
        """Drop the template ``fp`` matches (back to automatic detection)."""
        with self._lock:
            b = self._best(fp)
            if b is None:
                return None
            self._templates.remove(b[0])
            self._save()
            return b[0]

    def clear(self) -> None:
        with self._lock:
            self._templates = []
            self._save()
