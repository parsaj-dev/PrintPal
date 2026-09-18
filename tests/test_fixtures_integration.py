"""End-to-end detection tests against generated labels with REAL barcodes.

Unlike the unit tests (which inject fake barcodes), these render synthetic labels
carrying genuine Code128 barcodes and run the whole detect -> pyzbar decode ->
crop -> re-decode path. They need `python-barcode` (a dev dependency) and skip
cleanly when it is absent.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from printpal.config import Config
from printpal.detect import KIND_DOCUMENT, KIND_LABEL
from printpal.pipeline import process_file

_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "generate_fixtures.py"

barcode_missing = importlib.util.find_spec("barcode") is None
needs_barcode = pytest.mark.skipif(
    barcode_missing, reason="python-barcode not installed (pip install -e '.[dev]')")


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_fixtures", _TOOLS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    if barcode_missing:
        pytest.skip("python-barcode not installed")
    out = tmp_path_factory.mktemp("fixtures")
    _load_generator().generate(str(out))
    return out


@needs_barcode
class TestBareLabel:
    def test_single_confident_label(self, fixtures):
        labels = process_file(str(fixtures / "bare_4x6.pdf"), Config())
        assert len(labels) == 1
        lab = labels[0]
        assert lab.is_label and lab.kind == KIND_LABEL
        assert lab.result.barcodes_in >= 1
        assert lab.result.barcodes_out >= 1        # survives the crop
        assert lab.confidence >= 0.85

    def test_is_a_4in_wide_portrait_crop(self, fixtures):
        # The crop trims to content, so it is ~4in wide and clearly portrait --
        # not necessarily the full 6in of media when the label has bottom margin.
        lab = process_file(str(fixtures / "bare_4x6.pdf"), Config())[0]
        img = lab.preview_image
        w_in = img.width / lab.result.detect_dpi
        h_in = img.height / lab.result.detect_dpi
        assert 3.5 <= w_in <= 4.5
        assert h_in > w_in, "a 4x6 label crop should be portrait"


@needs_barcode
class TestLetterSheet:
    def test_label_found_and_cropped(self, fixtures):
        lab = process_file(str(fixtures / "letter_sheet.pdf"), Config())[0]
        assert lab.is_label
        assert lab.result.barcodes_in >= 1 and lab.result.barcodes_out >= 1
        # crop is far smaller than the full Letter sheet and stays in the top half
        x0, y0, x1, y1 = lab.result.box
        assert (x1 - x0) * (y1 - y0) < 0.5 * (1700 * 2200)
        assert y1 < 1400, "crop leaked into the instructions block below"


@needs_barcode
class TestMultiPage:
    def test_labels_and_slip_classified(self, fixtures):
        labels = process_file(str(fixtures / "multi.pdf"), Config())
        assert len(labels) == 3
        assert labels[0].kind == KIND_LABEL
        assert labels[1].kind == KIND_DOCUMENT      # the packing slip
        assert labels[2].kind == KIND_LABEL
        assert sum(1 for lab in labels if lab.is_label) == 2
