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
