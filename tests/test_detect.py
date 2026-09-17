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
    LabelResult, find_label, scale_box,
    _content_bbox, _rect_overlap, _union, _rotate_upright, _dominant_orientation,
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
    def __init__(self, left, top, width, height, orientation="UP", type="CODE128"):
        self.rect = _Rect(left, top, width, height)
        self.orientation = orientation
        self.type = type


def inject_barcodes(monkeypatch, barcodes):
    """Make detect's zbar decode return `barcodes` regardless of the image."""
    monkeypatch.setattr(detect, "zbar_decode", lambda img: list(barcodes))


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
