"""Incoming-folder watcher for the file-port install method.

When the "PrintPal" printer uses an in-box XPS driver bound to a local *file*
port, every print overwrites one file (e.g. ``incoming\\out.xps``). This watcher
runs in the background (registered at login by install_printer.ps1), grabs each
finished spool file the moment it is released, renames it out of the way so the
next job can't clobber it, and hands it to PrintPal.

Speed matters here -- this sits between "Print" and the label appearing:

* A job is taken the moment it is *complete*: an XPS (ZIP) ends with its
  end-of-central-directory record and a PDF with ``%%EOF``, both written last,
  so we poll every quarter second and act on that instead of waiting seconds for
  the size to settle. Size-stability remains as a fallback for anything odd.
* If PrintPal is already running, the job is moved straight into the spool it
  watches -- no new process, which on an old PC saves seconds per label. Only
  when it isn't running is ``PrintPal.exe --ingest`` launched.

Polling (not FileSystemWatcher) on purpose: it is simple, dependency-free, and
robust to the driver holding the file open until the job completes.

Run:  python printpal_watcher.py [--incoming DIR] [--printpal EXE] [--once]
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jobio  # noqa: E402

_WATCH_EXT = (".xps", ".oxps", ".pdf")
_POLL_SECONDS = 0.25
# Fallback when a file never shows a recognisable end marker: size unchanged
# for this many polls (~1.5 s) == fully written.
_STABLE_TICKS = 6


def _default_incoming() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("TEMP") or os.getcwd()
    return Path(base) / "PrintPal" / "spool" / "incoming"


def _logger(incoming: Path) -> logging.Logger:
    log = logging.getLogger("printpal.watcher")
    if not log.handlers:
        log.setLevel(logging.INFO)
        try:
            incoming.mkdir(parents=True, exist_ok=True)
            h = logging.FileHandler(incoming / "watcher.log", encoding="utf-8")
        except OSError:
            # A window-less build has no stderr; never crash on logging.
            h = logging.StreamHandler(sys.stderr) if sys.stderr else logging.NullHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    return log


def _is_ready(path: Path, seen: dict[Path, tuple[int, int]]) -> bool:
    """True once a job file is fully written: it ends with its format's end
    marker, or (fallback) its size has held steady across _STABLE_TICKS polls."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    last_size, ticks = seen.get(path, (-1, 0))
    if size == last_size and size > 0:
        ticks += 1
    else:
        ticks = 0
    seen[path] = (size, ticks)
    if size <= 0:
        return False
    return ticks >= _STABLE_TICKS or jobio.looks_complete(path)


def _read_head(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(16)


def _handoff(path: Path, exe: Path | None, log: logging.Logger,
             spool: Path | None = None) -> bool:
    """Claim ``path`` and deliver it. Returns False if it couldn't be claimed yet
    (e.g. the spooler still has it open), so the caller retries next poll."""
    # Rename out of the way first so a new print can't overwrite it mid-handoff.
    # The rename also fails while the spooler still holds the file open, which
    # is exactly the "not finished yet" signal we want.
    try:
        fmt = jobio.sniff_format(_read_head(path)) or jobio.FMT_XPS
        unique = jobio.staged_path(path.parent, fmt)
        os.replace(path, unique)
    except OSError as e:
        log.debug("Not ready to claim %s yet: %s", path.name, e)
        return False
    spool = spool or jobio.default_spool_dir()
    if jobio.app_is_running():
        try:
            dest = jobio.submit_to_spool(unique, spool, title=path.name)
            log.info("Delivered %s to the running PrintPal (%s)", unique.name, dest.name)
            return True
        except OSError as e:
            log.warning("Direct spool hand-off failed (%s); launching PrintPal", e)
    if exe is None:
        log.error("PrintPal.exe not found; leaving %s staged.", unique.name)
        return True
    log.info("Handing off %s -> PrintPal --ingest", unique.name)
    try:
        subprocess.Popen([str(exe), "--ingest", str(unique)], close_fds=True)
    except OSError as e:
        log.error("Failed to launch PrintPal: %s", e)
    return True


def scan_once(incoming: Path, exe: Path | None, seen: dict, log: logging.Logger) -> int:
    handled = 0
    for path in sorted(incoming.glob("*")):
        if path.suffix.lower() not in _WATCH_EXT:
            continue
        if path.name.startswith("printjob-"):
            continue  # already claimed / staged by us
        if _is_ready(path, seen) and _handoff(path, exe, log):
            seen.pop(path, None)
            handled += 1
    return handled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch the PrintPal printer's incoming folder.")
    parser.add_argument("--incoming")
    parser.add_argument("--printpal")
    parser.add_argument("--once", action="store_true", help="drain current files and exit")
    args = parser.parse_args(argv)

    incoming = Path(args.incoming) if args.incoming else _default_incoming()
    incoming.mkdir(parents=True, exist_ok=True)
    log = _logger(incoming)
    exe = jobio.find_printpal_exe(args.printpal)
    seen: dict = {}

    if args.once:
        # Treat any existing file as stable immediately.
        for path in sorted(incoming.glob("*")):
            if path.suffix.lower() in _WATCH_EXT and not path.name.startswith("printjob-"):
                _handoff(path, exe, log)
        return 0

    log.info("Watching %s (PrintPal.exe: %s)", incoming, exe)
    while True:
        try:
            scan_once(incoming, exe, seen, log)
        except Exception as e:  # noqa: BLE001 - a watcher must never die on one bad file
            log.warning("scan error: %s", e)
        time.sleep(_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
