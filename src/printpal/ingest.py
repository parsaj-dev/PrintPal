"""Job ingest / spool.

One decoupled hand-off point for every producer that feeds PrintPal a file to
crop and print: the Windows virtual printer, a second app launch, a future
Downloads-watcher, or the batch queue. Producers ``submit()`` a document into a
spool directory under AppData; the running instance ``claim()``s and processes
it. Nothing here imports tkinter, Qt or Windows APIs, so it is fully
cross-platform and unit-tested headless.

Robustness rules that matter for a printer feeding this concurrently:

* The document is written to a ``.part`` file and atomically renamed into place,
  so a reader never sees a half-written job.
* A JSON sidecar (``<uid>.ppjob``) is written *last* and is the "ready" signal;
  the document is guaranteed complete before the sidecar exists.
* ``claim()`` atomically renames the sidecar aside, so two drains (or a drain
  racing a producer) never process the same job twice.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from printpal.config import _CONFIG_DIR

SPOOL_DIR = _CONFIG_DIR / "spool"

# Sidecar extensions.
_READY = ".ppjob"      # a submitted, ready-to-process job
_TAKEN = ".pptaken"    # claimed by a consumer, processing in progress

# Documents/images the engine can ingest.
_ALLOWED_EXT = (".pdf", ".xps", ".oxps", ".png", ".jpg", ".jpeg",
                ".tif", ".tiff", ".bmp", ".gif", ".webp")

# Recognised origins (free-form, but these are the ones the app knows about).
ORIGIN_FILE = "file"
ORIGIN_PRINTER = "printer"
ORIGIN_HANDOFF = "handoff"
ORIGIN_DOWNLOADS = "downloads"
ORIGIN_BATCH = "batch"


@dataclass
class IngestJob:
    doc_path: Path        # the spooled document to process
    title: str            # human label (print job name, original filename, ...)
    origin: str           # where it came from (ORIGIN_*)
    submitted_at: float   # unix time the producer submitted it
    sidecar: Path         # the claimed sidecar, cleaned up by complete()


def _ensure_dir() -> None:
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)


def submit(src_path: str | os.PathLike, *, title: str | None = None,
           origin: str = ORIGIN_FILE, move: bool = False) -> Path:
    """Place ``src_path`` into the spool as a ready job and return its path.

    The bytes are copied (or moved) in full before the sidecar appears, so a
    consumer that scans for sidecars only ever sees complete documents.
    """
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(src)
    ext = src.suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError(f"Unsupported job type: {ext or 'unknown'}")

    _ensure_dir()
    uid = f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"
    dest = SPOOL_DIR / f"{uid}{ext}"
    part = SPOOL_DIR / f"{uid}{ext}.part"

    if move:
        # Try a cheap rename first; fall back to copy+remove across filesystems.
        try:
            os.replace(src, dest)
        except OSError:
            shutil.copy2(src, part)
            os.replace(part, dest)
            try:
                src.unlink()
            except OSError:
                pass
    else:
        shutil.copy2(src, part)
        os.replace(part, dest)

    meta = {
        "doc": dest.name,
        "title": title or src.name,
        "origin": origin,
        "submitted_at": time.time(),
    }
    sidecar = SPOOL_DIR / f"{uid}{_READY}"
    sidecar_part = SPOOL_DIR / f"{uid}{_READY}.part"
    sidecar_part.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(sidecar_part, sidecar)   # last, atomic: this is the ready signal
    return dest


def _read_job(sidecar: Path) -> IngestJob | None:
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    doc = SPOOL_DIR / str(meta.get("doc", ""))
    if not doc.is_file():
        return None
    return IngestJob(
        doc_path=doc,
        title=str(meta.get("title", doc.name)),
        origin=str(meta.get("origin", ORIGIN_FILE)),
        submitted_at=float(meta.get("submitted_at", 0.0) or 0.0),
        sidecar=sidecar,
    )


def pending() -> list[IngestJob]:
    """Ready jobs, oldest first. Does not claim them."""
    if not SPOOL_DIR.is_dir():
        return []
    jobs = []
    for sidecar in sorted(SPOOL_DIR.glob(f"*{_READY}")):
        job = _read_job(sidecar)
        if job is not None:
            jobs.append(job)
    jobs.sort(key=lambda j: j.submitted_at)
    return jobs


def claim() -> list[IngestJob]:
    """Atomically claim all ready jobs, oldest first, and return them.

    Each claimed job's sidecar is renamed to ``.pptaken`` so a concurrent drain
    can't pick it up again. Call ``complete()`` when done to remove the files.
    """
    if not SPOOL_DIR.is_dir():
        return []
    claimed: list[IngestJob] = []
    for sidecar in sorted(SPOOL_DIR.glob(f"*{_READY}")):
        taken = sidecar.with_suffix(_TAKEN)
        try:
            os.replace(sidecar, taken)
        except OSError:
            continue  # someone else claimed it between glob and rename
        job = _read_job(taken)
        if job is not None:
            claimed.append(job)
        else:
            _silent_unlink(taken)
    claimed.sort(key=lambda j: j.submitted_at)
    return claimed


def claim_one() -> IngestJob | None:
    """Atomically claim the oldest ready job, or None. Leaves other jobs ready."""
    for job in pending():
        taken = job.sidecar.with_suffix(_TAKEN)
        try:
            os.replace(job.sidecar, taken)
        except OSError:
            continue  # claimed by someone else; try the next
        claimed = _read_job(taken)
        if claimed is not None:
            return claimed
        _silent_unlink(taken)
    return None


def cleanup_stale() -> int:
    """Drop leftovers from a previous run: claimed sidecars, their documents and
    any half-written ``.part`` files. Returns the count removed. Ready jobs are
    left untouched so a job spooled while the app was closed still gets picked up.
    """
    if not SPOOL_DIR.is_dir():
        return 0
    removed = 0
    for taken in list(SPOOL_DIR.glob(f"*{_TAKEN}")):
        job = _read_job(taken)
        if job is not None:
            _silent_unlink(job.doc_path)
        _silent_unlink(taken)
        removed += 1
    for part in list(SPOOL_DIR.glob("*.part")):
        _silent_unlink(part)
        removed += 1
    return removed


def complete(job: IngestJob) -> None:
    """Remove a processed job's document and claimed sidecar (best effort)."""
    _silent_unlink(job.doc_path)
    _silent_unlink(job.sidecar)


def _silent_unlink(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def clear() -> None:
    """Delete every spooled job. Used on shutdown / for a clean slate."""
    if not SPOOL_DIR.is_dir():
        return
    for p in SPOOL_DIR.iterdir():
        _silent_unlink(p)


def _cli(argv: list[str] | None = None) -> int:
    """`python -m printpal.ingest <file> [--origin ...] [--title ...] [--move]`

    Submits a file to the spool so a running PrintPal drains it. Handy for the
    virtual-printer catcher and for manual testing.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="printpal.ingest",
                                     description="Submit a file to the PrintPal spool.")
    parser.add_argument("file", help="document or image to enqueue")
    parser.add_argument("--origin", default=ORIGIN_FILE)
    parser.add_argument("--title", default=None)
    parser.add_argument("--move", action="store_true",
                        help="move the source into the spool instead of copying")
    args = parser.parse_args(argv)
    dest = submit(args.file, title=args.title, origin=args.origin, move=args.move)
    print(dest)
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_cli(_sys.argv[1:]))
