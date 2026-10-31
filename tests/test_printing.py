"""Tests for the platform-independent parts of the printing module.

The GDI spooler call can't run off Windows, but the image preparation and DIB
packing are pure Pillow/struct and are where quality bugs hide, so they get
covered here.
"""
from __future__ import annotations

from PIL import Image

import pytest

from printpal.printing import (
    MODE_PERCENT, MODE_SIZE, PrintLayout, _dib_bytes, place_on_page, prepare_for_page,
)


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


def _label(w_in, h_in, dpi=300):
    img = Image.new("RGB", (int(w_in * dpi), int(h_in * dpi)), "white")
    img.info["dpi"] = (dpi, dpi)
    return img


class TestLayout:
    # A 4x6 page at 300 dpi.
    PAGE = (1200, 1800)

    def test_percent_100_is_actual_size(self):
        pl = place_on_page(_label(3, 4), *self.PAGE, layout=PrintLayout(mode=MODE_PERCENT))
        assert (pl.w, pl.h) == (900, 1200)
        assert (pl.x, pl.y) == (150, 300)            # centred by default
        assert pl.image.size == (900, 1200)

    def test_percent_uses_image_dpi(self):
        img = _label(3, 4, dpi=200)                 # 600x800 px at 200 dpi = 3x4 in
        pl = place_on_page(img, *self.PAGE, layout=PrintLayout(mode=MODE_PERCENT, percent=50))
        assert (pl.w, pl.h) == (450, 600)           # 1.5 x 2 in at 300 dpi

    def test_size_keep_aspect_fits_inside_box(self):
        lay = PrintLayout(mode=MODE_SIZE, width_in=2, height_in=2)
        pl = place_on_page(_label(4, 6), *self.PAGE, layout=lay)
        assert (pl.w, pl.h) == (400, 600)

    def test_size_stretch(self):
        lay = PrintLayout(mode=MODE_SIZE, width_in=3, height_in=3, keep_aspect=False)
        pl = place_on_page(_label(4, 6), *self.PAGE, layout=lay)
        assert (pl.w, pl.h) == (900, 900)

    def test_anchor_and_nudge(self):
        lay = PrintLayout(mode=MODE_PERCENT, anchor="bottom-right", offset_x_in=-0.5)
        pl = place_on_page(_label(2, 2), *self.PAGE, layout=lay)
        assert (pl.x, pl.y) == (1200 - 600 - 150, 1800 - 600)
        lay = PrintLayout(mode=MODE_PERCENT, anchor="top-left", offset_y_in=0.25)
        pl = place_on_page(_label(2, 2), *self.PAGE, layout=lay)
        assert (pl.x, pl.y) == (0, 75)

    def test_fit_anchor_top(self):
        pl = place_on_page(_label(4, 4), *self.PAGE, layout=PrintLayout(anchor="top"))
        assert (pl.x, pl.y, pl.w, pl.h) == (0, 0, 1200, 1200)

    def test_oversize_is_clipped_to_page(self):
        pl = place_on_page(_label(4, 6), *self.PAGE,
                           layout=PrintLayout(mode=MODE_PERCENT, percent=200))
        assert (pl.x, pl.y, pl.w, pl.h) == (0, 0, 1200, 1800)
        assert pl.image.size == (1200, 1800)

    def test_entirely_off_page_raises(self):
        lay = PrintLayout(mode=MODE_PERCENT, offset_x_in=9)
        with pytest.raises(ValueError):
            place_on_page(_label(2, 2), *self.PAGE, layout=lay)

    def test_huge_page_keeps_aspect_under_cap(self):
        # Letter at 1200 dpi exceeds the bitmap cap: the image shrinks evenly
        # and GDI stretches it back to the full device rectangle.
        pl = place_on_page(_label(8.5, 11, dpi=100), 10200, 13200)
        assert (pl.w, pl.h) == (10200, 13200)
        iw, ih = pl.image.size
        assert max(iw, ih) <= 6000
        assert abs(iw / ih - 10200 / 13200) < 0.01

    def test_describe(self):
        assert PrintLayout().describe() == "Fit"
        assert PrintLayout(mode=MODE_PERCENT, percent=85).describe() == "85%"
        assert PrintLayout(mode=MODE_SIZE, width_in=4, height_in=6).describe() == "4 × 6 in"


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
