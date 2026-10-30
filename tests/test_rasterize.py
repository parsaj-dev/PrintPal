"""Tests for rasterize helpers that don't need a real PDF."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from printpal.rasterize import DEFAULT_IMAGE_DPI, image_dpi, is_pdf, page_count, load_image


def test_is_pdf():
    assert is_pdf("foo.pdf")
    assert is_pdf("FOO.PDF")
    assert not is_pdf("foo.png")


def test_image_dpi_reads_metadata(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGB", (100, 100)).save(p, dpi=(300, 300))
    assert image_dpi(str(p)) == 300


def test_image_dpi_defaults_when_missing(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGB", (100, 100)).save(p)  # no dpi metadata
    assert image_dpi(str(p)) == DEFAULT_IMAGE_DPI


def test_image_dpi_ignores_bogus_low_value(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGB", (100, 100)).save(p, dpi=(1, 1))  # some encoders write 1 dpi
    assert image_dpi(str(p)) == DEFAULT_IMAGE_DPI


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
