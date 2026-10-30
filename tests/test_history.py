"""Tests for the SQLite print history + reprint store."""
from __future__ import annotations

import io
import time

from PIL import Image

from printpal.history import History, HistoryEntry


def _img(w=400, h=600, color=(10, 20, 30)):
    return Image.new("RGB", (w, h), color)


def _entry(**kw):
    base = dict(created_at=time.time(), source_name="label.pdf", carrier="UPS",
                tracking="1Z999AA10123456784", kind="label", copies=1,
                printer="DYMO LabelWriter 450", dpi=300)
    base.update(kw)
    return HistoryEntry(**base)


def test_record_and_read_back(tmp_path):
    h = History(base_dir=tmp_path)
    eid = h.record(_entry(), _img())
    assert eid > 0 and h.count() == 1
    got = h.get(eid)
    assert got.carrier == "UPS" and got.tracking == "1Z999AA10123456784"
    assert got.width_px == 400 and got.height_px == 600
    assert got.thumb_png and got.thumb_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_recent_is_newest_first(tmp_path):
    h = History(base_dir=tmp_path)
    h.record(_entry(created_at=100.0, tracking="A"), _img())
    h.record(_entry(created_at=200.0, tracking="B"), _img())
    recent = h.recent()
    assert [e.tracking for e in recent] == ["B", "A"]
    assert h.last().tracking == "B"


def test_reprint_image_round_trips(tmp_path):
    h = History(base_dir=tmp_path)
    eid = h.record(_entry(), _img(320, 480, (200, 100, 50)))
    entry = h.get(eid)
    img = h.load_print_image(entry)
    assert img is not None and img.size == (320, 480)


def test_thumbnail_is_bounded(tmp_path):
    h = History(base_dir=tmp_path)
    eid = h.record(_entry(), _img(1200, 1800))
    thumb = Image.open(io.BytesIO(h.get(eid).thumb_png))
    assert max(thumb.size) <= 240


def test_delete_removes_row_and_image(tmp_path):
    h = History(base_dir=tmp_path)
    eid = h.record(_entry(), _img())
    img_name = h.get(eid).image_name
    assert (tmp_path / "history" / img_name).exists()
    h.delete(eid)
    assert h.get(eid) is None and h.count() == 0
    assert not (tmp_path / "history" / img_name).exists()


def test_missing_image_returns_none(tmp_path):
    h = History(base_dir=tmp_path)
    eid = h.record(_entry(), _img())
    entry = h.get(eid)
    (tmp_path / "history" / entry.image_name).unlink()
    assert h.load_print_image(entry) is None
