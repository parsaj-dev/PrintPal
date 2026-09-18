"""PrintPal entry point.

Resolves an input file (CLI arg > clipboard), opens the main window, and lets it
drive detection, preview and printing. A single running instance is reused: a
second launch hands its file off to the window that is already open instead of
stacking up duplicates.
"""
from __future__ import annotations

import os
import sys
import traceback

from printpal.config import Config, _CONFIG_DIR
from printpal.log import get_logger

_HANDOFF_FILE = _CONFIG_DIR / "handoff.txt"
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


def _hand_off(path: str | None) -> None:
    """Deliver a file to the already-running instance via a handoff file."""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _HANDOFF_FILE.write_text(path or "", encoding="utf-8")
    except OSError:
        pass


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
        # Another instance owns the UI. A spooled job it will poll for; a plain
        # file open still goes through the handoff file.
        if input_path:
            log.info("Already running -- handing off %r and exiting.", input_path)
            _hand_off(input_path)
        else:
            log.info("Already running -- job spooled, exiting.")
        return

    try:
        _run(log, input_path)
    except Exception:
        log.error("Unhandled error:\n%s", traceback.format_exc())
        try:
            from printpal.ui import show_error
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

    # Clear any stale handoff before we start watching it.
    try:
        if _HANDOFF_FILE.exists():
            _HANDOFF_FILE.unlink()
    except OSError:
        pass

    # Clear last session's spooled jobs before we start watching for new ones.
    try:
        from printpal import ingest
        stale = ingest.cleanup_stale()
        if stale:
            log.info("Cleared %d stale spool item(s)", stale)
    except Exception as e:  # noqa: BLE001
        log.warning("Spool cleanup failed: %s", e)

    from printpal.ui import MainWindow
    window = MainWindow(config, initial_path=input_path)
    _install_handoff_watch(window)
    _install_spool_watch(window, log)
    _install_autoprint(window, config, log)
    log.info("Main window opened (initial file: %s)", input_path)
    window.run()


def _install_spool_watch(window, log) -> None:
    """Drain the ingest spool into the window: printed / watched jobs appear
    here the same as a dropped file. One job at a time so the preview keeps up;
    the batch queue (a later feature) will drain many at once."""
    def poll():
        try:
            if not getattr(window, "_busy", False):
                from printpal import ingest
                job = ingest.claim_one()
                if job is not None:
                    log.info("Ingesting spooled job %s (%s)", job.doc_path.name, job.origin)
                    try:
                        window.root.deiconify()
                        window.root.lift()
                        window.root.focus_force()
                    except Exception:
                        pass
                    window.load_path(str(job.doc_path))
        except Exception as e:  # noqa: BLE001
            log.warning("Spool poll failed: %s", e)
        try:
            window.root.after(700, poll)
        except Exception:
            pass
    window.root.after(700, poll)


def _install_handoff_watch(window) -> None:
    """Poll the handoff file so a second launch opens its file in this window."""
    def poll():
        try:
            if _HANDOFF_FILE.exists():
                text = _HANDOFF_FILE.read_text(encoding="utf-8").strip()
                _HANDOFF_FILE.unlink()
                try:
                    window.root.deiconify()
                    window.root.lift()
                    window.root.focus_force()
                except Exception:
                    pass
                if text and os.path.isfile(text):
                    window.load_path(text)
        except OSError:
            pass
        try:
            window.root.after(600, poll)
        except Exception:
            pass
    window.root.after(600, poll)


def _install_autoprint(window, config: Config, log) -> None:
    """When enabled, print a single high-confidence label without a click."""
    if not config.auto_print:
        return

    original = window._on_processing_done

    def wrapped(labels):
        original(labels)
        try:
            if (len(labels) == 1 and labels[0].is_printable
                    and labels[0].confidence >= config.auto_print_min_confidence):
                log.info("Auto-printing (confidence %.2f)", labels[0].confidence)
                window.root.after(200, window._print_current)
        except Exception as e:  # noqa: BLE001
            log.warning("Auto-print skipped: %s", e)

    window._on_processing_done = wrapped


if __name__ == "__main__":
    main()
