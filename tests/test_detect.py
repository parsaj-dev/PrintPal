"""Tests for the label detection and cropping pipeline.

Unit tests run always -- the barcode-dependent ones inject fake barcodes so they
need no real label image and no zbar decode. Integration tests against a real
label PDF run only when a fixture is present at tests/fixtures/sample_label.pdf.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from printpal import detect
from printpal.detect import (
    KIND_BLANK, KIND_DOCUMENT, KIND_LABEL,
    LabelResult, find_label, scale_box,
    _content_bbox, _rect_overlap, _union, _rotate_upright, _dominant_orientation,
    _cluster_1d, _widest_zero_mid, _axis_bounds, _barcode_payloads,
)
from printpal.rasterize import load_image, rasterize_pdf

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_PDF = FIXTURES / "sample_label.pdf"

needs_fixture = pytest.mark.skipif(
    not SAMPLE_PDF.exists(),
    reason=f"No fixture at {SAMPLE_PDF} -- drop a shipping label PDF there to run",
)


# -- fake barcode plumbing -----------------------------------------------------

class _Rect:
    def __init__(self, left, top, width, height):
        self.left, self.top, self.width, self.height = left, top, width, height


class _FakeBarcode:
    def __init__(self, left, top, width, height, orientation="UP", type="CODE128",
                 data=b"1Z999AA10123456784"):
        self.rect = _Rect(left, top, width, height)
        self.orientation = orientation
        self.type = type
        self.data = data


def inject_barcodes(monkeypatch, barcodes):
    """Make detect's zbar decode return `barcodes` regardless of the image."""
    monkeypatch.setattr(detect, "zbar_decode", lambda img, **kw: list(barcodes))


def blank(w, h):
    return Image.new("RGB", (w, h), (255, 255, 255))


def with_block(img, box, fill=(0, 0, 0)):
    """Paint a solid content block onto a page (drawn with numpy for speed)."""
    arr = np.array(img)
    x0, y0, x1, y1 = box
    arr[y0:y1, x0:x1] = fill
    return Image.fromarray(arr)


# -- geometry helpers ----------------------------------------------------------

class TestGeometry:
    def test_content_bbox_blank(self):
        assert _content_bbox(np.zeros((10, 10), dtype=np.uint8)) is None

    def test_content_bbox_tight(self):
        ink = np.zeros((100, 100), dtype=np.uint8)
        ink[20:40, 30:70] = 1
        assert _content_bbox(ink) == (30, 20, 70, 40)

    def test_rect_overlap(self):
        assert _rect_overlap((0, 0, 10, 10), (5, 5, 20, 20)) == 25
        assert _rect_overlap((0, 0, 10, 10), (20, 20, 30, 30)) == 0

    def test_union(self):
        assert _union((0, 0, 5, 5), (3, 3, 10, 12)) == (0, 0, 10, 12)

    def test_scale_box(self):
        assert scale_box((10, 20, 30, 40), 200, 300) == (15, 30, 45, 60)

    def test_scale_box_clamped(self):
        assert scale_box((10, 20, 300, 400), 200, 300, clip_w=100, clip_h=100) == (15, 30, 100, 100)


class TestOrientation:
    def test_dominant_by_area(self):
        bcs = [_FakeBarcode(0, 0, 10, 10, "UP"), _FakeBarcode(0, 0, 200, 50, "RIGHT")]
        assert _dominant_orientation(bcs) == "RIGHT"  # bigger barcode wins

    def test_rotate_upright_swaps_dims(self):
        img = Image.new("RGB", (100, 40))
        assert _rotate_upright(img, "RIGHT").size == (40, 100)
        assert _rotate_upright(img, "UP").size == (100, 40)


# -- detection regimes ---------------------------------------------------------

class TestBlankPage:
    def test_no_content(self):
        result = find_label(blank(400, 400), dpi=200)
        assert result.confidence == 0.0
        assert result.method == "no-content"


class TestLabelMedia:
    def test_full_page_label_with_barcode(self, monkeypatch):
        # 800x1200 @200dpi -> 4x6 in -> label media
        img = with_block(blank(800, 1200), (40, 40, 760, 1160))
        inject_barcodes(monkeypatch, [_FakeBarcode(100, 500, 400, 80, "UP")])
        r = find_label(img, dpi=200)
        assert r.is_full_page is True
        assert r.method == "label-media"
        assert r.confidence >= 0.85
        # crop hugs the content, well inside the page
        assert r.box[0] >= 0 and r.box[2] <= 800

    def test_label_media_without_barcode_keeps_whole_page(self, monkeypatch):
        img = with_block(blank(800, 1200), (40, 40, 400, 300))
        inject_barcodes(monkeypatch, [])
        r = find_label(img, dpi=200)
        assert r.is_full_page is True
        assert r.method == "label-media-no-barcode"
        assert r.box == (0, 0, 800, 1200)
        assert r.confidence < 0.6


class TestDocumentMedia:
    def _sheet(self):
        # 1700x2200 @200dpi -> 8.5x11 (Letter). Label block on top, instructions below.
        img = blank(1700, 2200)
        img = with_block(img, (300, 120, 1400, 780))     # label block
        img = with_block(img, (250, 1200, 1450, 2050))   # instruction block
        return img

    def test_barcode_block_excludes_instructions(self, monkeypatch):
        img = self._sheet()
        inject_barcodes(monkeypatch, [_FakeBarcode(500, 300, 500, 120, "UP")])
        r = find_label(img, dpi=200)
        assert r.is_full_page is False
        assert r.method == "barcode-block"
        # crop must stay in the top block, not swallow the instructions below
        assert r.box[3] < 1000, f"crop reached into instructions: {r.box}"
        assert r.box[1] < 300 and r.box[2] > 1000

    def test_barcode_far_apart_are_unioned(self, monkeypatch):
        # Two barcodes with a divider between them: both belong to the label.
        img = blank(1700, 2200)
        img = with_block(img, (200, 150, 700, 800))    # left half
        img = with_block(img, (1000, 150, 1500, 800))  # right half
        inject_barcodes(monkeypatch, [
            _FakeBarcode(250, 300, 300, 100, "UP"),
            _FakeBarcode(1050, 300, 300, 100, "UP"),
        ])
        r = find_label(img, dpi=200)
        assert r.box[0] < 300 and r.box[2] > 1400, f"union failed: {r.box}"

    def test_no_barcode_falls_back_low_confidence(self, monkeypatch):
        img = self._sheet()
        inject_barcodes(monkeypatch, [])
        r = find_label(img, dpi=200)
        assert r.confidence <= 0.4
        assert r.warnings


class TestOrientationApplied:
    def test_right_barcode_rotates_crop(self, monkeypatch):
        img = with_block(blank(800, 1200), (40, 40, 760, 1160))
        inject_barcodes(monkeypatch, [_FakeBarcode(100, 500, 400, 80, "RIGHT")])
        r = find_label(img, dpi=200)
        assert r.orientation == "RIGHT"
        # a RIGHT crop is rotated 90 deg, so the preview is wider than tall
        assert r.image.width > r.image.height


class TestKind:
    def test_blank_is_blank(self):
        assert find_label(blank(400, 400), dpi=200).kind == KIND_BLANK

    def test_label_media_is_label(self, monkeypatch):
        img = with_block(blank(800, 1200), (40, 40, 760, 1160))
        inject_barcodes(monkeypatch, [_FakeBarcode(100, 500, 400, 80, "UP")])
        r = find_label(img, dpi=200)
        assert r.kind == KIND_LABEL and r.is_label

    def test_document_with_barcode_is_label(self, monkeypatch):
        img = blank(1700, 2200)
        img = with_block(img, (300, 120, 1400, 780))
        inject_barcodes(monkeypatch, [_FakeBarcode(500, 300, 500, 120, "UP")])
        r = find_label(img, dpi=200)
        assert r.kind == KIND_LABEL

    def test_document_without_barcode_is_document(self, monkeypatch):
        # A packing slip / instructions sheet: content but no shipping barcode.
        img = with_block(blank(1700, 2200), (250, 200, 1450, 1400))
        inject_barcodes(monkeypatch, [])
        r = find_label(img, dpi=200)
        assert r.kind == KIND_DOCUMENT and not r.is_label


class TestGrowAligned:
    def test_stacked_label_blocks_reattach(self, monkeypatch):
        # A label split by the morphological close into an address block and a
        # barcode block with a small gap; instructions sit far below.
        img = blank(1700, 2200)
        img = with_block(img, (300, 150, 1400, 400))     # address block (top)
        img = with_block(img, (300, 520, 1400, 820))     # barcode block, gap 120px=0.6"
        img = with_block(img, (300, 1300, 1400, 2050))   # instructions, gap 480px=2.4"
        inject_barcodes(monkeypatch, [_FakeBarcode(500, 600, 500, 120, "UP")])
        r = find_label(img, dpi=200)
        assert r.box[1] < 400, f"top block not reattached: {r.box}"
        assert r.box[3] < 1000, f"instructions leaked into crop: {r.box}"

    def test_offset_column_not_grabbed(self, monkeypatch):
        # A block that doesn't share the label's column must not join. The x-gap
        # (120px) is wide enough that the morphological close leaves them separate.
        img = blank(1700, 2200)
        img = with_block(img, (200, 300, 700, 700))      # label (left)
        img = with_block(img, (820, 300, 1500, 700))     # neighbour column (right)
        inject_barcodes(monkeypatch, [_FakeBarcode(300, 400, 300, 120, "UP")])
        r = find_label(img, dpi=200)
        assert r.box[2] < 800, f"grabbed the neighbouring column: {r.box}"


class TestBarcodePayloads:
    def test_decoded_data_captured(self, monkeypatch):
        img = with_block(blank(800, 1200), (40, 40, 760, 1160))
        inject_barcodes(monkeypatch, [_FakeBarcode(100, 500, 400, 80, data=b"1Z12345")])
        r = find_label(img, dpi=200)
        assert r.barcode_data == ["1Z12345"]

    def test_payloads_dedupe_and_decode(self):
        bcs = [_FakeBarcode(0, 0, 10, 10, data=b"ABC"),
               _FakeBarcode(0, 0, 10, 10, data=b"ABC"),
               _FakeBarcode(0, 0, 10, 10, data="XYZ")]
        assert _barcode_payloads(bcs) == ["ABC", "XYZ"]


class TestNupHelpers:
    def test_cluster_1d_splits_on_gap(self):
        assert _cluster_1d([10, 20, 900, 910], gap=100) == [[10, 20], [900, 910]]

    def test_cluster_1d_single_group(self):
        assert _cluster_1d([10, 30, 50], gap=100) == [[10, 30, 50]]

    def test_widest_zero_mid(self):
        prof = np.ones(1000, dtype=int)
        prof[400:500] = 0            # a 100-wide gutter
        assert _widest_zero_mid(prof, 300, 700, min_run=50) == 450
        assert _widest_zero_mid(prof, 300, 700, min_run=200) is None

    def test_axis_bounds_two_columns(self):
        prof = np.ones(1600, dtype=int)
        prof[780:820] = 0            # gutter between two columns
        bounds = _axis_bounds([400.0, 1200.0], prof, dpi=200, page_len=1600)
        assert bounds[0] == 0 and bounds[-1] == 1600
        assert any(790 <= b <= 810 for b in bounds)

    def test_axis_bounds_no_split_single_cluster(self):
        prof = np.ones(1600, dtype=int)
        assert _axis_bounds([400.0, 500.0], prof, dpi=200, page_len=1600) == [0, 1600]


# -- integration against a real label -----------------------------------------

@pytest.fixture(scope="module")
def label_page() -> Image.Image:
    return rasterize_pdf(str(SAMPLE_PDF), dpi=200)


@pytest.fixture(scope="module")
def label_result(label_page) -> LabelResult:
    return find_label(label_page, dpi=200)


@needs_fixture
class TestRealLabel:
    def test_finds_barcodes(self, label_result):
        assert label_result.barcodes_in >= 1

    def test_confident(self, label_result):
        assert label_result.confidence >= 0.7

    def test_barcodes_survive_crop(self, label_result):
        assert label_result.barcodes_out >= 1


@needs_fixture
class TestLoadImage:
    def test_loads_pdf(self):
        img = load_image(str(SAMPLE_PDF), dpi=150)
        assert img.mode == "RGB" and img.width > 0

    def test_rejects_missing_page(self):
        with pytest.raises(ValueError, match="does not exist"):
            rasterize_pdf(str(SAMPLE_PDF), dpi=150, page=99)


class TestRetailBarcodes:
    """On Letter/A4 only shipping symbologies make a page a label: a packing
    slip's retail EAN/UPC (found only by the all-symbology rescan) must not."""

    def _decode_retail_only(self, monkeypatch, calls):
        def fake(img, **kw):
            calls.append(kw.get("symbols"))
            return [] if kw.get("symbols") else [_FakeBarcode(400, 400, 300, 90, type="EAN13")]
        monkeypatch.setattr(detect, "zbar_decode", fake)

    def test_document_media_skips_retail_fallback(self, monkeypatch):
        calls = []
        self._decode_retail_only(monkeypatch, calls)
        img = with_block(blank(1700, 2200), (250, 200, 1450, 1400))
        r = detect.find_labels(img, dpi=200)[0]
        assert r.kind == KIND_DOCUMENT
        assert all(c is not None for c in calls)   # never an all-symbology scan

    def test_label_media_still_uses_fallback(self, monkeypatch):
        calls = []
        self._decode_retail_only(monkeypatch, calls)
        img = with_block(blank(800, 1200), (40, 40, 760, 1160))
        r = detect.find_labels(img, dpi=200)[0]
        assert r.kind == KIND_LABEL and r.barcodes_in == 1
        assert None in calls
