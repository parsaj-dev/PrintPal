"""XPS ingest tests -- the virtual-printer path.

The "PrintPal" Windows printer emits XPS; MuPDF reads it natively, so a printed
job must flow through the exact same engine as a dropped PDF. These tests build
XPS documents the way an XPS driver would (via tools/xps.py) and run the real
engine over them -- no Windows, no print driver, no Ghostscript.
"""
from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from PIL import Image

from printpal.config import Config
from printpal.detect import KIND_LABEL
from printpal.pipeline import process_file
from printpal.rasterize import is_document, page_count, load_image, rasterize_pdf_region

barcode_missing = importlib.util.find_spec("barcode") is None


def _block_page(w=800, h=1200):
    arr = np.full((h, w, 3), 255, np.uint8)
    arr[60:h - 60, 60:w - 60] = 0
    return Image.fromarray(arr)


def test_xps_is_a_document():
    assert is_document("job.xps")
    assert is_document("JOB.OXPS")
    assert not is_document("job.png")


def test_xps_reads_and_rasterizes(tool_loader, tmp_path):
    xps = tool_loader("xps")
    out = str(tmp_path / "doc.xps")
    xps.pil_pages_to_xps([_block_page(), _block_page(1700, 2200)], out, dpi=200)
    assert page_count(out) == 2
    img0 = load_image(out, dpi=200, page=0)
    # 4x6in page rendered at 200 dpi -> ~800x1200 px
    assert abs(img0.width - 800) <= 4 and abs(img0.height - 1200) <= 4
    region = rasterize_pdf_region(out, (0, 0, 400, 600), 200, 300, page=0)
    assert region.width > 0 and region.height > 0


def test_xps_pipeline_classifies_pages(tool_loader, tmp_path):
    xps = tool_loader("xps")
    out = str(tmp_path / "doc.xps")
    xps.pil_pages_to_xps([_block_page()], out, dpi=200)
    labels = process_file(out, Config())
    assert len(labels) == 1
    assert labels[0].is_printable
    # print-quality re-render comes from the XPS region, same as a PDF
    assert labels[0].render_print_image(Config()).width > 0


@pytest.mark.skipif(barcode_missing, reason="python-barcode not installed")
def test_xps_from_real_label_detects(tool_loader, tmp_path):
    gen = tool_loader("generate_fixtures")
    xps = tool_loader("xps")
    label = gen.make_label_image()                 # 4x6 with a real Code128 barcode
    sheet = Image.new("RGB", (1700, 2200), "white")
    sheet.paste(label, (100, 100))                 # label on a Letter sheet
    out = str(tmp_path / "printed.xps")
    xps.pil_pages_to_xps([label, sheet], out, dpi=200)

    labels = process_file(out, Config())
    assert len(labels) == 2
    for lab in labels:
        assert lab.kind == KIND_LABEL
        assert lab.result.barcodes_in >= 1
        assert lab.result.barcodes_out >= 1        # barcode survives the crop
