"""Tests for the platform-independent parts of the printing module.

The GDI spooler call can't run off Windows, but the image preparation and DIB
packing are pure Pillow/struct and are where quality bugs hide, so they get
covered here.
"""
from __future__ import annotations

from PIL import Image

from printpal.printing import prepare_for_page, _dib_bytes


class TestPrepareForPage:
    def test_portrait_into_portrait_centers(self):
        img = Image.new("RGB", (1176, 1800))
        resized, ox, oy = prepare_for_page(img, 1200, 1800)
        assert resized.size == (1176, 1800)
        assert ox == 12 and oy == 0

    def test_preserves_aspect(self):
        img = Image.new("RGB", (600, 1200))
        resized, ox, oy = prepare_for_page(img, 1200, 1800)
        # scale limited by height: 1800/1200 = 1.5 -> 900x1800
        assert resized.size == (900, 1800)
        assert ox == 150

    def test_fit_rotate_fills_media(self):
        landscape = Image.new("RGB", (1800, 1176))
        resized, ox, oy = prepare_for_page(landscape, 1200, 1800, fit_rotate=True)
        # rotated to portrait to fill 4x6 media
        assert resized.height > resized.width
        assert resized.size == (1176, 1800)

    def test_no_fit_rotate_keeps_orientation(self):
        landscape = Image.new("RGB", (1800, 1176))
        resized, ox, oy = prepare_for_page(landscape, 1200, 1800, fit_rotate=False)
        assert resized.width > resized.height
        assert oy > 0

    def test_zero_page_is_safe(self):
        img = Image.new("RGB", (100, 100))
        resized, ox, oy = prepare_for_page(img, 0, 0)
        assert resized.size == (100, 100)


class TestDib:
    def test_header_is_40_bytes(self):
        _raw, header, iw, ih = _dib_bytes(Image.new("RGB", (10, 10)))
        assert len(header) == 40
        assert (iw, ih) == (10, 10)

    def test_row_padding_to_four_bytes(self):
        # width 101 -> 303 bytes/row -> padded to 304
        raw, _header, iw, ih = _dib_bytes(Image.new("RGB", (101, 5)))
        assert len(raw) == 304 * 5

    def test_no_padding_when_aligned(self):
        # width 100 -> 300 bytes/row, already 4-aligned
        raw, _header, _iw, ih = _dib_bytes(Image.new("RGB", (100, 4)))
        assert len(raw) == 300 * 4
