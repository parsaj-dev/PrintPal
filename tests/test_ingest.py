"""Tests for the job spool (ingest.py). Fully headless: SPOOL_DIR is redirected
into a tmp dir so nothing touches the real AppData folder."""
from __future__ import annotations

import time

import pytest

from printpal import ingest


@pytest.fixture
def spool(tmp_path, monkeypatch):
    d = tmp_path / "spool"
    monkeypatch.setattr(ingest, "SPOOL_DIR", d)
    return d


def _src(tmp_path, name="label.pdf", data=b"%PDF-1.4 fake"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_submit_then_pending(spool, tmp_path):
    src = _src(tmp_path)
    dest = ingest.submit(src, title="My Label", origin=ingest.ORIGIN_PRINTER)
    assert dest.exists() and dest.parent == spool
    jobs = ingest.pending()
    assert len(jobs) == 1
    assert jobs[0].title == "My Label"
    assert jobs[0].origin == ingest.ORIGIN_PRINTER
    assert jobs[0].doc_path == dest


def test_ready_signal_is_the_sidecar(spool, tmp_path):
    ingest.submit(_src(tmp_path))
    # the document keeps its extension; the sidecar is the .ppjob ready-marker
    assert list(spool.glob("*.pdf"))
    assert list(spool.glob("*.ppjob"))
    assert not list(spool.glob("*.part"))


def test_claim_is_exclusive(spool, tmp_path):
    ingest.submit(_src(tmp_path))
    first = ingest.claim()
    assert len(first) == 1
    # once claimed, the ready sidecar is gone and a second drain sees nothing
    assert ingest.claim() == []
    assert not list(spool.glob("*.ppjob"))
    assert list(spool.glob("*.pptaken"))


def test_complete_removes_files(spool, tmp_path):
    ingest.submit(_src(tmp_path))
    job = ingest.claim()[0]
    ingest.complete(job)
    assert not job.doc_path.exists()
    assert not job.sidecar.exists()
    assert list(spool.iterdir()) == []


def test_unsupported_extension_rejected(spool, tmp_path):
    bad = _src(tmp_path, name="notes.txt", data=b"hi")
    with pytest.raises(ValueError):
        ingest.submit(bad)


def test_move_consumes_source(spool, tmp_path):
    src = _src(tmp_path, name="moveme.pdf")
    ingest.submit(src, move=True)
    assert not src.exists()
    assert len(ingest.pending()) == 1


def test_pending_is_oldest_first(spool, tmp_path):
    a = ingest.submit(_src(tmp_path, name="a.pdf"), title="a")
    time.sleep(0.01)
    b = ingest.submit(_src(tmp_path, name="b.pdf"), title="b")
    titles = [j.title for j in ingest.pending()]
    assert titles == ["a", "b"]
    assert {a.name, b.name} == {j.doc_path.name for j in ingest.pending()}


def test_empty_spool(spool):
    assert ingest.pending() == []
    assert ingest.claim() == []
