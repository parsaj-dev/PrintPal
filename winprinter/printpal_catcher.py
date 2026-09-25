"""PrintPal print-job catcher.

Sits between a Windows virtual printer and the PrintPal engine. A redirection
port monitor (or the file-port watcher) invokes this program with the spooled
job -- on stdin, or as a file -- and it:

    1. sniffs the format (PDF / XPS / PostScript),
    2. stages it to a file,
    3. converts PostScript to PDF *only* if a Ghostscript path is supplied
       (Ghostscript is AGPL and never bundled -- see winprinter/README.md),
    4. hands the file to PrintPal: straight into the spool a running instance
       watches, or via ``PrintPal.exe --ingest <file>`` when it isn't running.
       Either way it drives the same crop+print engine as a dropped file.

It imports nothing from the PrintPal package -- the only contract is the
``--ingest`` CLI -- so the virtual-printer component stays cleanly separable.

There is no console when a port monitor runs this, so everything is logged to
``<staging>\\catcher.log`` for diagnosis.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jobio  # noqa: E402  (sibling module)


def _default_staging() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("TEMP") or os.getcwd()
    return Path(base) / "PrintPal" / "spool" / "incoming"


def _log(staging: Path) -> logging.Logger:
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log = logging.getLogger("printpal.catcher")
    if not log.handlers:
        log.setLevel(logging.INFO)
        try:
            h = logging.FileHandler(staging / "catcher.log", encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
        except OSError:
            log.addHandler(logging.StreamHandler(sys.stderr) if sys.stderr
                           else logging.NullHandler())
    return log


def _read_input(file: str | None) -> bytes:
    if file:
        return Path(file).read_bytes()
    # Binary stdin -- this is how a redirection port monitor delivers the job.
    return sys.stdin.buffer.read()


def _ghostscript_to_pdf(ps_path: Path, gs_exe: str, log: logging.Logger) -> Path | None:
    pdf_path = ps_path.with_suffix(".pdf")
    cmd = [gs_exe, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
           f"-sOutputFile={pdf_path}", str(ps_path)]
    log.info("Converting PostScript via Ghostscript: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as e:
        log.error("Ghostscript conversion failed: %s", e)
        return None
    return pdf_path if pdf_path.is_file() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Catch a print job and hand it to PrintPal.")
    parser.add_argument("--file", help="read the job from this file instead of stdin")
    parser.add_argument("--staging", help="staging directory")
    parser.add_argument("--printpal", help="path to PrintPal.exe")
    parser.add_argument("--ghostscript", help="path to gswin64c.exe (enables PostScript, AGPL)")
    parser.add_argument("--no-launch", action="store_true", help="stage only; don't call PrintPal")
    args = parser.parse_args(argv)

    staging = Path(args.staging) if args.staging else _default_staging()
    log = _log(staging)

    try:
        data = _read_input(args.file)
    except OSError as e:
        log.error("Could not read the job: %s", e)
        return 2
    if not data:
        log.error("Empty print job -- nothing to do.")
        return 2

    try:
        fmt, staged = jobio.write_stream(data, staging)
    except ValueError as e:
        log.error("%s (%d bytes)", e, len(data))
        return 3
    log.info("Staged %d bytes as %s (%s)", len(data), staged.name, fmt)

    if fmt == jobio.FMT_PS:
        if not args.ghostscript:
            log.error("PostScript job but no --ghostscript given; cannot convert. "
                      "Install an XPS/PDF driver, or enable Ghostscript (AGPL).")
            return 4
        pdf = _ghostscript_to_pdf(staged, args.ghostscript, log)
        if pdf is None:
            return 4
        staged = pdf

    if args.no_launch:
        print(staged)
        return 0

    if jobio.app_is_running():
        # PrintPal is open: drop the job straight into its spool -- no process
        # launch, so the label shows up almost immediately.
        try:
            dest = jobio.submit_to_spool(staged, jobio.default_spool_dir())
            log.info("Delivered %s to the running PrintPal (%s)", staged.name, dest.name)
            return 0
        except OSError as e:
            log.warning("Direct spool hand-off failed (%s); launching PrintPal", e)

    exe = jobio.find_printpal_exe(args.printpal)
    if exe is None:
        log.error("PrintPal.exe not found. Set PRINTPAL_EXE or pass --printpal.")
        return 5
    log.info("Handing off to %s --ingest %s", exe, staged.name)
    try:
        subprocess.Popen([str(exe), "--ingest", str(staged)], close_fds=True)
    except OSError as e:
        log.error("Failed to launch PrintPal: %s", e)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
