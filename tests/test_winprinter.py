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
