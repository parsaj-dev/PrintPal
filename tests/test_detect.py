"""Tests for the label detection and cropping pipeline.

Uses the real FedEx return label fixture to verify end-to-end detection.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode

from printpal.rasterize import load_image, rasterize_pdf
from printpal.detect import (
    find_label, LabelResult,
    _content_runs, _merge_runs_around, _measure_gaps, _adaptive_gap_threshold,
)

FIXTURES = Path(__file__).parent / "fixtures"
FEDEX_PDF = FIXTURES / "fedex_return_label.pdf"


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
        # should sit between the intra-label gaps (~6) and the big gaps (~48)
        assert 6 < thr < 48


# -- integration tests against the real fixture --------------------------------

@pytest.fixture(scope="module")
def fedex_page() -> Image.Image:
    assert FEDEX_PDF.exists(), f"Fixture not found: {FEDEX_PDF}"
    return rasterize_pdf(str(FEDEX_PDF), dpi=200)


@pytest.fixture(scope="module")
def fedex_result(fedex_page) -> LabelResult:
    return find_label(fedex_page, dpi=200)


class TestFedexLabel:
    def test_finds_barcodes(self, fedex_result):
        assert fedex_result.barcodes_in >= 1, "Should find at least one barcode in the source"

    def test_method_is_barcode_anchored(self, fedex_result):
        assert fedex_result.method == "barcode-anchored"

    def test_high_confidence(self, fedex_result):
        assert fedex_result.confidence >= 0.7

    def test_output_barcodes_still_scan(self, fedex_result):
        assert fedex_result.barcodes_out >= 1, "Barcodes must still scan in the cropped output"

    def test_cropped_smaller_than_page(self, fedex_result, fedex_page):
        pw, ph = fedex_page.size
        cw, ch = fedex_result.image.size
        assert cw < pw or ch < ph, "Crop should be smaller than the full page"

    def test_excludes_instruction_text(self, fedex_result, fedex_page):
        # the cropped label should be well under half the page area
        page_area = fedex_page.width * fedex_page.height
        crop_area = fedex_result.image.width * fedex_result.image.height
        assert crop_area < page_area * 0.6, (
            f"Crop area ({crop_area}) is too large relative to page ({page_area}). "
            "Probably includes instruction text."
        )

    def test_no_warnings(self, fedex_result):
        assert fedex_result.warnings == [], f"Unexpected warnings: {fedex_result.warnings}"


# -- edge cases ---------------------------------------------------------------

class TestBlankPage:
    def test_no_content(self):
        blank = Image.new("RGB", (400, 400), (255, 255, 255))
        result = find_label(blank, dpi=200)
        assert result.confidence == 0.0
        assert result.method == "no-content"


class TestLoadImage:
    def test_loads_pdf(self):
        if FEDEX_PDF.exists():
            img = load_image(str(FEDEX_PDF), dpi=150)
            assert img.mode == "RGB"
            assert img.width > 0

    def test_rejects_missing_page(self):
        if FEDEX_PDF.exists():
            with pytest.raises(ValueError, match="does not exist"):
                rasterize_pdf(str(FEDEX_PDF), dpi=150, page=99)
