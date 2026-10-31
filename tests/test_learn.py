"""Tests for crop memory (learn.py) and its use in the pipeline.

Pages are synthetic Letter sheets at 100 DPI with injected barcodes, so the
tests run anywhere: a label block (text lines + barcode) at the top left and an
instructions block below it -- the same template drawn several times with a
different offset and different address lengths, like two real labels.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from printpal import detect, learn
from printpal.config import Config
from printpal.pipeline import process_file

DPI = 100
W, H = 850, 1100   # Letter at 100 DPI


class _Rect:
    def __init__(self, l, t, w, h):
        self.left, self.top, self.width, self.height = l, t, w, h


class _FakeBarcode:
    def __init__(self, l, t, w, h, kind="CODE128", orientation="UP"):
        self.rect = _Rect(l, t, w, h)
        self.orientation = orientation
        self.type = kind
        self.data = b"123456789012"


def _page(dx=0, dy=0, address_lines=4, line_len=260, barcode_kind="CODE128",
          barcode_at=(100, 400), barcode_w=280):
    """Return (image, barcodes) for one instance of the template."""
    a = np.full((H, W), 255, np.uint8)
    ox, oy = 60 + dx, 60 + dy
    a[oy:oy + 30, ox:ox + 300] = 0                       # carrier band
    for i in range(address_lines):                       # address lines
        y = oy + 60 + i * 22
        a[y:y + 10, ox:ox + line_len - 15 * i] = 0
    bx, by = barcode_at[0] + dx, barcode_at[1] + dy
    a[by:by + 80, bx:bx + barcode_w] = 0                 # the barcode
    a[oy + 530:oy + 540, ox:ox + 200] = 0                # a reference line
    for i in range(6):                                    # instructions below
        y = 800 + i * 20
        a[y:y + 8, 60:780] = 0
    return Image.fromarray(a).convert("RGB"), [_FakeBarcode(bx, by, barcode_w, 80, barcode_kind)]


def _fp(img, barcodes):
    ink = learn.ink_of(img)
    boxes = [((b.rect.left, b.rect.top, b.rect.left + b.rect.width, b.rect.top + b.rect.height),
              b.orientation) for b in barcodes]
    return learn.fingerprint(ink, DPI, boxes, [b.type for b in barcodes])


# -- fingerprint matching ------------------------------------------------------

class TestMatch:
    def test_same_template_shifted_matches_with_offset(self):
        a = _fp(*_page())
        b = _fp(*_page(dx=15, dy=10, address_lines=3))
        m = learn.match(a, b)
        assert m is not None
        assert m[1] == pytest.approx(0.15, abs=0.01)
        assert m[2] == pytest.approx(0.10, abs=0.01)

    def test_barcode_elsewhere_is_another_template(self):
        a = _fp(*_page())
        b = _fp(*_page(barcode_at=(100, 650)))   # 2.5" lower
        assert learn.match(a, b) is None

    def test_other_symbology_is_another_template(self):
        a = _fp(*_page())
        b = _fp(*_page(barcode_kind="QRCODE"))
        assert learn.match(a, b) is None

    def test_page_size_must_agree(self):
        a = _fp(*_page())
        img, bc = _page()
        b = _fp(img.crop((0, 0, 600, 900)), bc)   # 6x9" page
        assert learn.match(a, b) is None

    def test_one_with_barcode_one_without(self):
        img, bc = _page()
        assert learn.match(_fp(img, bc), _fp(img, [])) is None

    def test_barcodeless_pages_match_on_layout(self):
        img, _ = _page()
        img2, _ = _page(address_lines=3)
        assert learn.match(_fp(img, []), _fp(img2, [])) is not None
        blank_ish = Image.new("RGB", (W, H), "white")
        arr = np.asarray(blank_ish).copy()
        arr[900:1050, 400:800] = 0   # content in a totally different place
        assert learn.match(_fp(img, []), _fp(Image.fromarray(arr), [])) is None

    def test_fingerprint_round_trips_json(self):
        fp = _fp(*_page())
        again = learn.Fingerprint.from_json(json.loads(json.dumps(fp.to_json())))
        assert learn.match(fp, again)[0] > 1.0


# -- snapping ------------------------------------------------------------------

class TestSnap:
    def setup_method(self):
        img, _ = _page()
        self.snap = learn.Snapper(learn.ink_of(img), DPI)

    def test_rough_box_snaps_to_label_content(self):
        # The label runs (60,60)-(380,600). A sloppy box that clips the left
        # edge, the right end of the barcode and the bottom line, and pokes into
        # white space above: everything clipped comes back, the white goes.
        got = self.snap.snap((70, 45, 340, 597))
        assert got == (60, 60, 380, 600)

    def test_block_mostly_outside_is_dropped(self):
        # The box reaches 30 px into the instructions block below.
        got = self.snap.snap((50, 50, 400, 830))
        assert got[3] <= 600

    def test_box_on_white_space_is_unchanged(self):
        assert self.snap.snap((500, 100, 700, 300)) == (500, 100, 700, 300)

    def test_box_never_grows_far(self):
        # A small box on the far left of a full-width instructions line must not
        # stretch across the page to take the whole line.
        got = self.snap.snap((55, 795, 200, 815))
        assert got[2] <= 200 + learn.SNAP_MAX_GROW_IN * DPI


# -- memory --------------------------------------------------------------------

class TestMemory:
    def test_learn_find_and_persist(self, tmp_path):
        path = tmp_path / "crop_memory.json"
        mem = learn.CropMemory(path)
        fp = _fp(*_page())
        mem.learn(fp, (60, 60, 360, 600), DPI, name="FedEx labels on Letter paper", inside=1)
        again = learn.CropMemory(path)
        hit = again.find(_fp(*_page(dx=20, dy=-10)))
        assert hit is not None and hit.name == "FedEx labels on Letter paper"
        box, sample = hit.crop(DPI, (W, H))
        assert box == (80, 50, 380, 590)   # followed the 0.2" / -0.1" shift
        assert sample.inside == 1

    def test_unrelated_page_not_found(self, tmp_path):
        mem = learn.CropMemory(tmp_path / "m.json")
        mem.learn(_fp(*_page()), (60, 60, 360, 600), DPI, name="x")
        assert mem.find(_fp(*_page(barcode_at=(450, 100)))) is None

    def test_corrections_are_averaged(self, tmp_path):
        mem = learn.CropMemory(tmp_path / "m.json")
        fp = _fp(*_page())
        for box in [(60, 60, 360, 600), (50, 62, 362, 610), (61, 59, 380, 602)]:
            mem.learn(fp, box, DPI, name="x")
        assert len(mem) == 1
        box, _ = mem.find(fp).crop(DPI, (W, H))
        assert box == (60, 60, 362, 602)   # per-edge median

    def test_change_of_mind_replaces(self, tmp_path):
        mem = learn.CropMemory(tmp_path / "m.json")
        fp = _fp(*_page())
        mem.learn(fp, (60, 60, 360, 600), DPI, name="x")
        mem.learn(fp, (60, 60, 360, 600), DPI, name="x")
        mem.learn(fp, (60, 380, 360, 600), DPI, name="x")   # just the barcode now
        box, _ = mem.find(fp).crop(DPI, (W, H))
        assert box == (60, 380, 360, 600)

    def test_whole_page(self, tmp_path):
        mem = learn.CropMemory(tmp_path / "m.json")
        fp = _fp(*_page())
        mem.learn(fp, (0, 0, W, H), DPI, name="x", whole=True)
        box, sample = mem.find(fp).crop(DPI, (W, H))
        assert sample.whole and box == (0, 0, W, H)

    def test_forget_and_clear(self, tmp_path):
        mem = learn.CropMemory(tmp_path / "m.json")
        fp = _fp(*_page())
        other = _fp(*_page(barcode_at=(450, 100)))
        mem.learn(fp, (60, 60, 360, 600), DPI, name="a")
        mem.learn(other, (400, 50, 800, 300), DPI, name="b")
        assert len(mem) == 2
        assert mem.forget(fp).name == "a"
        assert mem.find(fp) is None and mem.find(other) is not None
        mem.clear()
        assert len(learn.CropMemory(tmp_path / "m.json")) == 0

    def test_damaged_file_is_empty_memory(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("{not json", encoding="utf-8")
        assert len(learn.CropMemory(path)) == 0
        path.write_text(json.dumps({"version": 1, "templates": [{"id": "x"}]}), encoding="utf-8")
        assert len(learn.CropMemory(path)) == 0


def test_describe_layouts():
    fp = _fp(*_page())
    assert learn.describe(fp, "FedEx") == "FedEx labels on Letter paper"
    assert learn.describe(fp, "Unknown") == "labels like this on Letter paper"
    img, _ = _page()
    assert learn.describe(_fp(img, []), None) == "pages like this on Letter paper"
    assert learn.paper_name((4.0, 6.0)) == "4×6"


# -- pipeline ------------------------------------------------------------------

def _save(tmp_path, name, img):
    p = tmp_path / name
    img.save(p, dpi=(DPI, DPI))
    return str(p)


@pytest.fixture
def cfg():
    return Config(detect_dpi=DPI, print_dpi=DPI, crop_margin_inches=0.05)


def _with_barcodes(monkeypatch, barcodes):
    monkeypatch.setattr(detect, "zbar_decode", lambda img, **kw: list(barcodes))


def test_learned_crop_is_used_on_the_next_label(tmp_path, monkeypatch, cfg):
    mem = learn.CropMemory(tmp_path / "m.json")
    img, bc = _page()
    _with_barcodes(monkeypatch, bc)
    first = process_file(_save(tmp_path, "a.png", img), cfg, memory=mem)[0]
    assert first.learned is None
    # The user taught: just the barcode and the reference line under it.
    mem.learn(_fp(img, bc), (95, 395, 385, 605), DPI, name="FedEx labels on Letter paper",
              inside=1)

    img2, bc2 = _page(dx=12, dy=8, address_lines=2)
    _with_barcodes(monkeypatch, bc2)
    lab = process_file(_save(tmp_path, "b.png", img2), cfg, memory=mem)[0]
    assert lab.learned == "FedEx labels on Letter paper"
    assert lab.result.method == "learned"
    assert lab.result.confidence >= 0.95 and not lab.result.warnings
    # Snapped to this label's own barcode and the line under it (shifted 12/8
    # px; the line, mostly inside the taught box, comes in whole), plus 5 px margin.
    assert lab.result.box == (60 + 12 - 5, 400 + 8 - 5, 380 + 12 + 5, 600 + 8 + 5)


def test_learned_crop_that_loses_a_barcode_warns(tmp_path, monkeypatch, cfg):
    mem = learn.CropMemory(tmp_path / "m.json")
    img, bc = _page()
    mem.learn(_fp(img, bc), (95, 395, 385, 605), DPI, name="x", inside=1, exact=True)
    # Same template, but this label's barcode is wider (a longer payload), so
    # the exact (unsnapped) box clips it.
    img2, bc2 = _page(barcode_w=340)
    _with_barcodes(monkeypatch, bc2)
    lab = process_file(_save(tmp_path, "b.png", img2), cfg, memory=mem)[0]
    assert lab.result.method == "learned"
    assert lab.result.confidence < cfg.auto_print_min_confidence
    assert lab.result.warnings


def test_without_memory_nothing_changes(tmp_path, monkeypatch, cfg):
    mem = learn.CropMemory(tmp_path / "m.json")
    img, bc = _page()
    mem.learn(_fp(img, bc), (95, 395, 385, 605), DPI, name="x", inside=1)
    _with_barcodes(monkeypatch, bc)
    lab = process_file(_save(tmp_path, "a.png", img), cfg)[0]
    assert lab.learned is None and lab.result.method != "learned"


def test_restore_auto_undoes_a_learned_crop(tmp_path, monkeypatch, cfg):
    img, bc = _page()
    _with_barcodes(monkeypatch, bc)
    path = _save(tmp_path, "a.png", img)
    auto = process_file(path, cfg)[0].result
    mem = learn.CropMemory(tmp_path / "m.json")
    mem.learn(_fp(img, bc), (95, 395, 385, 605), DPI, name="x", inside=1)
    lab = process_file(path, cfg, memory=mem)[0]
    assert lab.result.box != auto.box and lab.auto_box == auto.box
    lab.restore_auto()
    assert lab.result.box == auto.box
    assert lab.result.method == auto.method
    assert lab.result.confidence == auto.confidence
    assert lab.learned is None
