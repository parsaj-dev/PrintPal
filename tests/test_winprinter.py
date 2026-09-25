"""Tests for the winprinter component's OS-independent logic (format sniffing,
staging, and the catcher's routing). The Windows plumbing in the .ps1 scripts is
covered by the manual test plan in winprinter/README.md."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_WIN = Path(__file__).resolve().parent.parent / "winprinter"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"winprinter_{name}", _WIN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jobio = _load("jobio")


class TestSniff:
    def test_pdf(self):
        assert jobio.sniff_format(b"%PDF-1.7\n...") == jobio.FMT_PDF

    def test_xps_zip(self):
        assert jobio.sniff_format(b"PK\x03\x04rest of zip") == jobio.FMT_XPS

    def test_postscript(self):
        assert jobio.sniff_format(b"%!PS-Adobe-3.0\n") == jobio.FMT_PS

    def test_unknown(self):
        assert jobio.sniff_format(b"random bytes") == jobio.FMT_UNKNOWN

    def test_empty(self):
        assert jobio.sniff_format(b"") == jobio.FMT_UNKNOWN


class TestWriteStream:
    def test_writes_pdf(self, tmp_path):
        fmt, dest = jobio.write_stream(b"%PDF-1.4 body", tmp_path)
        assert fmt == jobio.FMT_PDF
        assert dest.suffix == ".pdf"
        assert dest.read_bytes() == b"%PDF-1.4 body"

    def test_writes_xps(self, tmp_path):
        fmt, dest = jobio.write_stream(b"PK\x03\x04xpsdata", tmp_path)
        assert fmt == jobio.FMT_XPS and dest.suffix == ".xps"

    def test_rejects_unknown(self, tmp_path):
        with pytest.raises(ValueError):
            jobio.write_stream(b"not a document", tmp_path)


class TestCatcher:
    def setup_method(self):
        self.catcher = _load("printpal_catcher")

    def test_stage_only_pdf(self, tmp_path, capsys):
        src = tmp_path / "in.pdf"
        src.write_bytes(b"%PDF-1.5 hello")
        rc = self.catcher.main(["--file", str(src), "--staging", str(tmp_path / "stg"),
                                "--no-launch"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert Path(out).is_file() and Path(out).suffix == ".pdf"

    def test_unknown_stream_errors(self, tmp_path):
        src = tmp_path / "in.bin"
        src.write_bytes(b"garbage")
        rc = self.catcher.main(["--file", str(src), "--staging", str(tmp_path / "stg"),
                                "--no-launch"])
        assert rc == 3

    def test_postscript_without_ghostscript_errors(self, tmp_path):
        src = tmp_path / "in.ps"
        src.write_bytes(b"%!PS-Adobe-3.0\nshowpage")
        rc = self.catcher.main(["--file", str(src), "--staging", str(tmp_path / "stg"),
                                "--no-launch"])
        assert rc == 4  # needs Ghostscript, which is opt-in


class TestWatcher:
    def setup_method(self):
        self.watcher = _load("printpal_watcher")

    def test_once_claims_spooled_file(self, tmp_path, monkeypatch):
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        (incoming / "out.xps").write_bytes(b"PK\x03\x04 fake xps payload")
        # No PrintPal.exe in the sandbox: the file is still claimed/renamed so a
        # following print can't clobber it; the hand-off is just logged.
        monkeypatch.setattr(self.watcher.jobio, "find_printpal_exe", lambda *_a, **_k: None)
        rc = self.watcher.main(["--once", "--incoming", str(incoming)])
        assert rc == 0
        assert not (incoming / "out.xps").exists()
        assert list(incoming.glob("printjob-*.xps"))


def test_port_dispatcher_imports():
    port = _load("printpal_port")
    assert port.main(["bogus"]) == 64  # usage error for an unknown subcommand


class TestCompletion:
    def test_xps_complete_only_with_end_record(self, tmp_path):
        import zipfile
        p = tmp_path / "out.xps"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("FixedDocSeq.fdseq", "<x/>" * 100)
        assert jobio.looks_complete(p)
        p.write_bytes(p.read_bytes()[:-22])      # cut off the central-directory end
        assert not jobio.looks_complete(p)

    def test_pdf_complete_on_eof(self, tmp_path):
        p = tmp_path / "out.pdf"
        p.write_bytes(b"%PDF-1.7\n" + b"x" * 5000)
        assert not jobio.looks_complete(p)
        p.write_bytes(p.read_bytes() + b"\n%%EOF\n")
        assert jobio.looks_complete(p)

    def test_missing_file(self, tmp_path):
        assert not jobio.looks_complete(tmp_path / "nope.xps")


class TestDirectSpool:
    def test_spool_contract_matches_the_app(self, tmp_path, monkeypatch):
        """What the watcher writes, printpal.ingest must claim as a ready job."""
        from printpal import ingest
        monkeypatch.setattr(ingest, "SPOOL_DIR", tmp_path / "spool")
        doc = tmp_path / "printjob-1.xps"
        doc.write_bytes(b"PK\x03\x04 data")
        dest = jobio.submit_to_spool(doc, tmp_path / "spool", title="out.xps")
        assert not doc.exists() and dest.exists()
        job = ingest.claim_one()
        assert job is not None
        assert job.doc_path == dest and job.title == "out.xps"
        assert job.origin == ingest.ORIGIN_PRINTER

    def test_app_not_running_off_windows(self):
        assert jobio.app_is_running() is False


class TestFastWatcher:
    def setup_method(self):
        self.watcher = _load("printpal_watcher")

    def _complete_xps(self, path):
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("a.fpage", "<FixedPage/>")

    def test_complete_job_is_taken_on_first_poll(self, tmp_path, monkeypatch):
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        self._complete_xps(incoming / "out.xps")
        monkeypatch.setattr(self.watcher.jobio, "app_is_running", lambda: True)
        monkeypatch.setattr(self.watcher.jobio, "default_spool_dir", lambda: tmp_path / "spool")
        log = self.watcher._logger(incoming)
        assert self.watcher.scan_once(incoming, None, {}, log) == 1
        assert not (incoming / "out.xps").exists()
        assert list((tmp_path / "spool").glob("*.ppjob"))     # delivered, no launch

    def test_partial_job_waits(self, tmp_path):
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        (incoming / "out.xps").write_bytes(b"PK\x03\x04 still being written")
        log = self.watcher._logger(incoming)
        seen: dict = {}
        assert self.watcher.scan_once(incoming, None, seen, log) == 0
        assert (incoming / "out.xps").exists()

    def test_launches_printpal_when_not_running(self, tmp_path, monkeypatch):
        incoming = tmp_path / "incoming"
        incoming.mkdir()
        self._complete_xps(incoming / "out.xps")
        launched = []
        monkeypatch.setattr(self.watcher.jobio, "app_is_running", lambda: False)
        monkeypatch.setattr(self.watcher.subprocess, "Popen",
                            lambda args, **kw: launched.append(args))
        log = self.watcher._logger(incoming)
        exe = tmp_path / "PrintPal.exe"
        assert self.watcher.scan_once(incoming, exe, {}, log) == 1
        assert launched and launched[0][1] == "--ingest"
