"""PrintPal entry point.

Resolves an input file (CLI arg > clipboard), opens the main window, and lets it
drive detection, preview and printing. A single running instance is reused: a
second launch (or a print to the virtual printer) delivers its file through the
ingest spool, which the running window polls, instead of stacking up duplicates.
"""
from __future__ import annotations

import os
import sys
import traceback

from printpal.config import Config
from printpal.log import get_logger

_MUTEX_NAME = "Global\\PrintPalSingleInstance"


def _acquire_mutex():
    """Grab a system-wide named mutex. Returns the handle, or None if another
    instance already holds it. No-op (returns True) off Windows."""
    if sys.platform != "win32":
        return True
    import win32event
    import win32api
    import winerror
    mutex = win32event.CreateMutex(None, False, _MUTEX_NAME)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        win32api.CloseHandle(mutex)
        return None
    return mutex


def _release_mutex(handle) -> None:
    if handle in (None, True) or sys.platform != "win32":
        return
    import win32api
    win32api.CloseHandle(handle)


def _parse_args(argv: list[str]) -> tuple[str | None, str | None]:
    """Split argv into (open_path, ingest_path).

    ``--ingest <file>`` is how the "PrintPal" virtual printer (and any other
    producer) hands a rendered job to the app: it is spooled and drained rather
    than opened as a one-off file. A bare path argument is opened as before.
    """
    ingest_path = None
    positional = []
    it = iter(argv[1:])
    for arg in it:
        if arg == "--ingest":
            ingest_path = next(it, None)
        elif arg.startswith("--ingest="):
            ingest_path = arg.split("=", 1)[1]
        else:
            positional.append(arg)
    open_path = None
    if positional:
        cand = positional[0].strip().strip('"')
        if os.path.isfile(cand):
            open_path = cand
    if ingest_path:
        ingest_path = ingest_path.strip().strip('"')
    return open_path, ingest_path


def _resolve_input(open_path: str | None) -> str | None:
    """Fall back to a clipboard file when no path was given on the CLI."""
    if open_path and os.path.isfile(open_path):
        return open_path
    try:
        from printpal.clipboard import get_pdf_path
        return get_pdf_path()
    except Exception:
        return None


def _resolve_printer(config: Config, log) -> None:
    """If the saved printer isn't installed, fall back to the system default or
    the first available printer -- no modal, the UI lets the user change it."""
    try:
        from printpal.printing import printer_exists, list_printers, default_printer
    except Exception:
        return
    try:
        if config.printer and printer_exists(config.printer):
            return
        fallback = default_printer()
        if not fallback:
            printers = list_printers()
            fallback = printers[0] if printers else None
        if fallback:
            log.info("Configured printer %r missing; using %r", config.printer, fallback)
            config.printer = fallback
            config.save()
    except Exception as e:  # noqa: BLE001
        log.warning("Printer resolution failed: %s", e)


def _ensure_printer_watcher(log) -> None:
    """If the virtual printer is installed, make sure its hidden watcher task is
    running (e.g. after it was stopped). A no-op when it's already running (the
    task allows only one instance) or when the printer was never installed."""
    if sys.platform != "win32":
        return
    import subprocess
    try:
        subprocess.run(["schtasks", "/Run", "/TN", "PrintPalPortWatcher"],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa: BLE001
        log.debug("Watcher task not started: %s", e)


def main() -> None:
    log = get_logger()
    log.info("PrintPal started")

    open_path, ingest_path = _parse_args(sys.argv)

    # A printed / watched job is spooled so the running instance (or this one, on
    # startup) drains it through the same engine as a dropped file.
    if ingest_path and os.path.isfile(ingest_path):
        try:
            from printpal import ingest
            dest = ingest.submit(ingest_path, origin=ingest.ORIGIN_PRINTER, move=True)
            log.info("Spooled ingest job %r -> %s", ingest_path, dest.name)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to spool ingest job %r: %s", ingest_path, e)

    input_path = None if ingest_path else _resolve_input(open_path)

    mutex = _acquire_mutex()
    if mutex is None:
        # Another instance owns the UI. Deliver via the spool, which it polls.
        if input_path:
            try:
                from printpal import ingest
                ingest.submit(input_path, origin=ingest.ORIGIN_HANDOFF)
                log.info("Already running -- spooled %r and exiting.", input_path)
            except Exception as e:  # noqa: BLE001
                log.warning("Handoff spool failed: %s", e)
        else:
            log.info("Already running -- job spooled, exiting.")
        return

    try:
        _run(log, input_path)
    except Exception:
        log.error("Unhandled error:\n%s", traceback.format_exc())
        try:
            from printpal.qtui.app import show_error
            show_error("PrintPal Error",
                       "Something went wrong. Check the log file for details.")
        except Exception:
            pass
    finally:
        _release_mutex(mutex)
        log.info("PrintPal exiting")


def _run(log, input_path: str | None) -> None:
    config = Config.load()
    log.info("Config loaded: printer=%s, detect_dpi=%d, print_dpi=%d",
             config.printer, config.detect_dpi, config.print_dpi)
    _resolve_printer(config, log)

    # Clear last session's spooled jobs before we start watching for new ones.
    try:
        from printpal import ingest
        stale = ingest.cleanup_stale()
        if stale:
            log.info("Cleared %d stale spool item(s)", stale)
    except Exception as e:  # noqa: BLE001
        log.warning("Spool cleanup failed: %s", e)

    _ensure_printer_watcher(log)

    log.info("Main window opening (initial file: %s)", input_path)
    from printpal.qtui.app import run_app
    run_app(config, initial_path=input_path, log=log)


if __name__ == "__main__":
    main()
