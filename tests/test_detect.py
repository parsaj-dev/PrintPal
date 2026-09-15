"""Tests for the label detection and cropping pipeline.

Unit tests run always. Integration tests require a label PDF fixture at
tests/fixtures/sample_label.pdf -- drop any shipping label PDF there and
they'll run. Skipped when the fixture is absent (e.g. CI without fixtures).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from printpal.rasterize import load_image, rasterize_pdf
from printpal.detect import (
    find_label, LabelResult,
    _content_runs, _merge_runs_around, _measure_gaps, _adaptive_gap_threshold,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_PDF = FIXTURES / "sample_label.pdf"

needs_fixture = pytest.mark.skipif(
    not SAMPLE_PDF.exists(),
    reason=f"No fixture at {SAMPLE_PDF} -- drop a shipping label PDF there to run integration tests",
)


# -- unit tests for internal helpers ------------------------------------------

class TestContentRuns:
    def test_empty(self):
        import numpy as np
        assert _content_runs(np.array([], dtype=bool)) == []

    def test_single_run(self):
        import numpy as np
        mask = np.array([False, True, True, True, False])
        assert _content_runs(mask) == [(1, 4)]

    def test_two_runs(self):
        import numpy as np
        mask = np.array([True, True, False, False, True])
        assert _content_runs(mask) == [(0, 2), (4, 5)]


class TestMergeRuns:
    def test_merges_small_gap(self):
        runs = [(0, 10), (15, 25)]
        x0, x1 = _merge_runs_around(runs, gap_px=10, lo=0, hi=10)
        assert x0 == 0 and x1 == 25

    def test_preserves_large_gap(self):
        runs = [(0, 10), (50, 60)]
        x0, x1 = _merge_runs_around(runs, gap_px=10, lo=0, hi=10)
        assert x0 == 0 and x1 == 10


class TestAdaptiveGap:
    def test_few_gaps_returns_fallback(self):
        assert _adaptive_gap_threshold([5, 10], fallback_px=30) == 30

    def test_finds_jump(self):
        gaps = [3, 5, 4, 6, 50, 55, 48]
        thr = _adaptive_gap_threshold(gaps, fallback_px=30)
        assert 6 < thr < 48


# -- integration tests against a real label ------------------------------------

@pytest.fixture(scope="module")
def label_page() -> Image.Image:
    return rasterize_pdf(str(SAMPLE_PDF), dpi=200)


@pytest.fixture(scope="module")
def label_result(label_page) -> LabelResult:
    return find_label(label_page, dpi=200)


@needs_fixture
class TestLabelDetection:
    def test_finds_barcodes(self, label_result):
        assert label_result.barcodes_in >= 1, "Should find at least one barcode"

    def test_method_is_barcode_anchored(self, label_result):
        assert label_result.method == "barcode-anchored"

    def test_high_confidence(self, label_result):
        assert label_result.confidence >= 0.7

    def test_output_barcodes_still_scan(self, label_result):
        assert label_result.barcodes_out >= 1, "Barcodes must still scan after cropping"

    def test_cropped_smaller_than_page(self, label_result, label_page):
        pw, ph = label_page.size
        cw, ch = label_result.image.size
        assert cw < pw or ch < ph, "Crop should be smaller than the full page"

    def test_excludes_instruction_text(self, label_result, label_page):
        page_area = label_page.width * label_page.height
        crop_area = label_result.image.width * label_result.image.height
        assert crop_area < page_area * 0.6, (
            f"Crop area ({crop_area}) too large relative to page ({page_area})"
        )


# -- edge cases ---------------------------------------------------------------

class TestBlankPage:
    def test_no_content(self):
        blank = Image.new("RGB", (400, 400), (255, 255, 255))
        result = find_label(blank, dpi=200)
        assert result.confidence == 0.0
        assert result.method == "no-content"


@needs_fixture
class TestLoadImage:
    def test_loads_pdf(self):
        img = load_image(str(SAMPLE_PDF), dpi=150)
        assert img.mode == "RGB"
        assert img.width > 0

    def test_rejects_missing_page(self):
        with pytest.raises(ValueError, match="does not exist"):
            rasterize_pdf(str(SAMPLE_PDF), dpi=150, page=99)
