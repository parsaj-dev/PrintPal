"""Tests for rasterize helpers that don't need a real PDF."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from printpal.rasterize import is_pdf, page_count, load_image


def test_is_pdf():
    assert is_pdf("foo.pdf")
    assert is_pdf("FOO.PDF")
    assert not is_pdf("foo.png")


def test_page_count_image(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGB", (10, 10)).save(p)
    assert page_count(str(p)) == 1


def test_load_image_png(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGBA", (10, 10)).save(p)
    img = load_image(str(p))
    assert img.mode == "RGB"
    assert img.size == (10, 10)
