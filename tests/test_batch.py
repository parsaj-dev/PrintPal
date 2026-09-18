"""Tests for the batch print queue. Printing is a fake callable and detection
uses an injected barcode, so the whole flow runs headless."""
from __future__ import annotations

import numpy as np
from PIL import Image

from printpal import detect
from printpal.batch import PrintQueue, QUEUED, DONE, FAILED
from printpal.config import Config
from printpal.history import History


class _Rect:
    def __init__(self, l, t, w, h):
        self.left, self.top, self.width, self.height = l, t, w, h


class _FakeBarcode:
    def __init__(self, data=b"1Z999AA10123456784"):
        self.rect = _Rect(100, 800, 400, 80)
        self.orientation = "UP"
        self.type = "CODE128"
        self.data = data


def _label_png(tmp_path, name):
    arr = np.full((1200, 800, 3), 255, np.uint8)
    arr[40:1160, 40:760] = 0
    p = tmp_path / name
    Image.fromarray(arr).save(p, dpi=(200, 200))
    return str(p)


class _FakePrinter:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first
        self._n = 0

    def __call__(self, image, printer, copies):
        self._n += 1
        if self.fail_first and self._n == 1:
            raise RuntimeError("printer offline")
        self.calls.append((printer, copies, image.size))


def _inject(monkeypatch, data=b"1Z999AA10123456784"):
    monkeypatch.setattr(detect, "zbar_decode", lambda img: [_FakeBarcode(data)])


def test_add_expands_to_label_items(tmp_path, monkeypatch):
    _inject(monkeypatch)
    q = PrintQueue(Config(), _FakePrinter())
    items = q.add_many([_label_png(tmp_path, "a.png"), _label_png(tmp_path, "b.png")])
    assert len(items) == 2
    assert all(it.status == QUEUED for it in items)
    assert all(it.carrier == "UPS" and it.tracking == "1Z999AA10123456784" for it in items)


def test_print_all_marks_done_and_records_history(tmp_path, monkeypatch):
    _inject(monkeypatch)
    printer = _FakePrinter()
    hist = History(base_dir=tmp_path / "hist")
    q = PrintQueue(Config(), printer, history=hist)
    q.add_many([_label_png(tmp_path, "a.png"), _label_png(tmp_path, "b.png")])
    q.print_all()
    assert all(it.status == DONE for it in q.items)
    assert len(printer.calls) == 2
    assert hist.count() == 2                 # each print logged with a reprint image


def test_failure_then_retry(tmp_path, monkeypatch):
    _inject(monkeypatch)
    printer = _FakePrinter(fail_first=True)
    q = PrintQueue(Config(), printer)
    items = q.add_many([_label_png(tmp_path, "a.png"), _label_png(tmp_path, "b.png")])
    q.print_all()
    statuses = sorted(it.status for it in items)
    assert statuses == [DONE, FAILED]
    failed = next(it for it in items if it.status == FAILED)
    assert failed.error and failed.retries == 1
    # retry the failed one with a now-healthy printer
    q.retry(failed)
    assert failed.status == DONE


def test_unreadable_source_becomes_failed_item(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not really a pdf")
    q = PrintQueue(Config(), _FakePrinter())
    items = q.add(str(bad))
    assert len(items) == 1 and items[0].status == FAILED and items[0].error


def test_routing_directs_labels(tmp_path, monkeypatch):
    _inject(monkeypatch)
    printer = _FakePrinter()
    cfg = Config()
    cfg.thermal_printer = "Zebra"
    cfg.paper_printer = "OfficeJet"
    q = PrintQueue(cfg, printer)
    q.add(_label_png(tmp_path, "a.png"))
    q.print_all(routing_enabled=True)
    assert printer.calls[0][0] == "Zebra"    # a label goes to the thermal printer


def test_on_change_callback_fires(tmp_path, monkeypatch):
    _inject(monkeypatch)
    seen = []
    q = PrintQueue(Config(), _FakePrinter(), on_change=lambda it: seen.append(it.status))
    q.add(_label_png(tmp_path, "a.png"))
    q.print_all()
    assert QUEUED in seen and DONE in seen
