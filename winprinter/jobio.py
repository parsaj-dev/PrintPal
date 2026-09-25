"""Format sniffing and staging for captured print jobs.

The winprinter component is deliberately decoupled from the PrintPal engine: it
captures whatever a Windows print driver spools, works out what it is, writes it
to a staging file, and hands that file to ``PrintPal.exe --ingest``. Everything
here is pure/file-only so it can be unit-tested on any OS -- the Windows-specific
plumbing lives in the .ps1 scripts and the thin catcher entry point.

Recognised formats:
* PDF  (``%PDF``)                         -> handed straight to the engine
* XPS/OXPS (a ZIP/OPC package, ``PK\\x03\\x04``) -> read natively by MuPDF, no Ghostscript
* PostScript (``%!PS``/``%!``)            -> needs Ghostscript (AGPL, opt-in) to become PDF
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

FMT_PDF = "pdf"
FMT_XPS = "xps"
FMT_PS = "ps"
FMT_UNKNOWN = "unknown"

_EXT = {FMT_PDF: ".pdf", FMT_XPS: ".xps", FMT_PS: ".ps"}

# Must match printpal.main._MUTEX_NAME: held by the running PrintPal window.
APP_MUTEX = "Global\\PrintPalSingleInstance"

# The spool contract shared with printpal.ingest (kept literal so this component
# never imports the app): the document is moved in first, then a JSON sidecar
# "<uid>.ppjob" appears atomically as the ready signal.
_SPOOL_READY = ".ppjob"
ORIGIN_PRINTER = "printer"


def sniff_format(data: bytes) -> str:
    """Identify a spooled print stream from its leading bytes."""
    if not data:
        return FMT_UNKNOWN
    head = data[:16]
    if head.startswith(b"%PDF"):
        return FMT_PDF
    if head.startswith(b"PK\x03\x04"):
        # OPC/ZIP package -- XPS and OpenXPS are both ZIPs. Good enough here; the
        # engine (MuPDF) does the real validation when it opens the file.
        return FMT_XPS
    if head.startswith(b"%!PS") or head.startswith(b"%!"):
        return FMT_PS
    # Some XPS spool streams are wrapped; fall back to a ZIP-central-directory
    # probe only if the magic is missing but the tail looks like a ZIP.
    if data[-22:].find(b"PK\x05\x06") != -1:
        return FMT_XPS
    return FMT_UNKNOWN


def staged_path(staging_dir: str | Path, fmt: str) -> Path:
    """A unique path under ``staging_dir`` for a job of the given format."""
    ext = _EXT.get(fmt, ".bin")
    uid = f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"
    return Path(staging_dir) / f"printjob-{uid}{ext}"


def write_stream(data: bytes, staging_dir: str | Path) -> tuple[str, Path]:
    """Sniff ``data``, write it to a staged file, and return (format, path).

    Raises ValueError for an unrecognised stream so the caller can log and skip.
    """
    fmt = sniff_format(data)
    if fmt == FMT_UNKNOWN:
        raise ValueError("Unrecognised print stream (not PDF, XPS or PostScript)")
    Path(staging_dir).mkdir(parents=True, exist_ok=True)
    dest = staged_path(staging_dir, fmt)
    dest.write_bytes(data)
    return fmt, dest


def find_printpal_exe(explicit: str | None = None) -> Path | None:
    """Locate PrintPal.exe: an explicit path, the env override, then the usual
    install locations. Returns None if it can't be found."""
    import os

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("PRINTPAL_EXE")
    if env:
        candidates.append(Path(env))
    for base in filter(None, (os.environ.get("ProgramFiles"),
                              os.environ.get("ProgramFiles(x86)"),
                              os.environ.get("LOCALAPPDATA"))):
        candidates.append(Path(base) / "PrintPal" / "PrintPal.exe")
    # Next to this component (portable layout)
    candidates.append(Path(__file__).resolve().parent.parent / "PrintPal.exe")
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def looks_complete(path: str | Path) -> bool:
    """True once a spooled job has been written out in full.

    XPS is a ZIP, and a ZIP's *last* record is the end-of-central-directory
    (``PK\\x05\\x06``); a PDF ends with ``%%EOF``. Both are written last, so
    seeing them means the driver has finished -- no need to wait seconds for
    the file size to stop changing.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < 22:
                return False
            # The EOCD sits in the last 22 bytes plus an optional comment
            # (almost always empty); 1 KB covers it and a PDF's trailer.
            f.seek(max(0, size - 1024))
            tail = f.read()
    except OSError:
        return False
    return b"PK\x05\x06" in tail[-(22 + 256):] or b"%%EOF" in tail


def default_spool_dir() -> Path:
    """The spool the PrintPal app watches (printpal.ingest.SPOOL_DIR)."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "PrintPal" / "spool"


def app_is_running() -> bool:
    """True if a PrintPal window is running (it holds the single-instance
    mutex). Uses ctypes, so the watcher needs no pywin32."""
    if sys.platform != "win32":
        return False
    import ctypes
    synchronize = 0x00100000
    k32 = ctypes.windll.kernel32
    handle = k32.OpenMutexW(synchronize, False, APP_MUTEX)
    if not handle:
        return False
    k32.CloseHandle(handle)
    return True


def submit_to_spool(doc: str | Path, spool_dir: str | Path, title: str | None = None,
                    origin: str = ORIGIN_PRINTER) -> Path:
    """Hand a job straight to a *running* PrintPal by moving it into the spool
    it watches -- no process launch, so it shows up almost instantly.

    Mirrors ``printpal.ingest.submit(move=True)``: the document is renamed into
    place first and the sidecar (written to a temp name, then renamed) is the
    ready signal, so the app never sees a half-delivered job.
    """
    src = Path(doc)
    spool = Path(spool_dir)
    spool.mkdir(parents=True, exist_ok=True)
    uid = f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"
    dest = spool / f"{uid}{src.suffix.lower()}"
    os.replace(src, dest)
    meta = {"doc": dest.name, "title": title or src.name, "origin": origin,
            "submitted_at": time.time()}
    part = spool / f"{uid}{_SPOOL_READY}.part"
    part.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(part, spool / f"{uid}{_SPOOL_READY}")
    return dest
