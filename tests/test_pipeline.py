"""Tests for the orchestration layer (pipeline.py).

These use image files (no PDF needed) and injected barcodes, so they run
anywhere and still exercise process_file, manual rotation and print rendering.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from printpal import detect
from printpal.config import Config
from printpal.pipeline import process_file, ProcessedLabel
from printpal.detect import find_label, LabelResult


class _Rect:
    def __init__(self, l, t, w, h):
        self.left, self.top, self.width, self.height = l, t, w, h


class _FakeBarcode:
    def __init__(self, l, t, w, h, orientation="UP"):
        self.rect = _Rect(l, t, w, h)
        self.orientation = orientation
        self.type = "CODE128"


def _label_png(tmp_path, name="label.png", size=(800, 1200)):
    arr = np.full((size[1], size[0], 3), 255, np.uint8)
    arr[40:size[1] - 40, 40:size[0] - 40] = 0
    p = tmp_path / name
    Image.fromarray(arr).save(p)
    return str(p)


def test_process_image_file(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "zbar_decode",
                        lambda img: [_FakeBarcode(100, 500, 400, 80, "UP")])
    path = _label_png(tmp_path)
    cfg = Config()
    labels = process_file(path, cfg)
    assert len(labels) == 1
    lab = labels[0]
    assert isinstance(lab, ProcessedLabel)
    assert lab.is_printable
    assert lab.page_count == 1


def test_manual_rotation_changes_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "zbar_decode",
                        lambda img: [_FakeBarcode(100, 500, 400, 80, "UP")])
    path = _label_png(tmp_path)
    labels = process_file(path, Config())
    lab = labels[0]
    before = lab.preview_image.size
    lab.rotate_cw()
    after = lab.preview_image.size
    assert after == (before[1], before[0])
    assert lab.manual_rotation == 90
    lab.rotate_ccw()
    assert lab.manual_rotation == 0


def test_render_print_image_for_image_source(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "zbar_decode",
                        lambda img: [_FakeBarcode(100, 500, 400, 80, "UP")])
    path = _label_png(tmp_path)
    cfg = Config()
    lab = process_file(path, cfg)[0]
    printed = lab.render_print_image(cfg)
    assert printed.width > 0 and printed.height > 0
    # cached
    assert lab.render_print_image(cfg) is printed


def test_render_print_image_applies_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, "zbar_decode",
                        lambda img: [_FakeBarcode(100, 500, 400, 80, "UP")])
    path = _label_png(tmp_path)
    cfg = Config()
    lab = process_file(path, cfg)[0]
    upright = lab.render_print_image(cfg)
    lab.rotate_cw()
    rotated = lab.render_print_image(cfg)
    assert rotated.size == (upright.size[1], upright.size[0])


def test_image_dpi_metadata_drives_dimensions(tmp_path, monkeypatch):
    # A 1200x1800 image tagged 300 dpi is a 4x6 label, not a 6x9 sheet.
    monkeypatch.setattr(detect, "zbar_decode",
                        lambda img: [_FakeBarcode(100, 500, 400, 80, "UP")])
    arr = np.full((1800, 1200, 3), 255, np.uint8)
    arr[60:1740, 60:1140] = 0
    p = tmp_path / "label300.png"
    Image.fromarray(arr).save(p, dpi=(300, 300))
    lab = process_file(str(p), Config())[0]
    assert lab.result.detect_dpi == 300
    img = lab.preview_image
    assert round(img.width / lab.result.detect_dpi) == 4
    assert round(img.height / lab.result.detect_dpi) == 6


def test_image_without_dpi_uses_default(tmp_path, monkeypatch):
    from printpal.rasterize import DEFAULT_IMAGE_DPI
    monkeypatch.setattr(detect, "zbar_decode",
                        lambda img: [_FakeBarcode(100, 500, 400, 80, "UP")])
    path = _label_png(tmp_path)  # PNG saved without dpi metadata
    lab = process_file(path, Config())[0]
    assert lab.result.detect_dpi == DEFAULT_IMAGE_DPI


def test_packing_slip_is_not_counted_as_label(tmp_path, monkeypatch):
    # A document-sized page with content but no barcode is a slip, not a label.
    monkeypatch.setattr(detect, "zbar_decode", lambda img: [])
    arr = np.full((2200, 1700, 3), 255, np.uint8)
    arr[200:1400, 250:1450] = 0
    p = tmp_path / "slip.png"
    Image.fromarray(arr).save(p, dpi=(200, 200))
    lab = process_file(str(p), Config())[0]
    assert lab.is_printable          # still selectable if the user wants it
    assert not lab.is_label          # but not a shipping label
    assert lab.kind == "document"
