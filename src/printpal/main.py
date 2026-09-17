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


def _resolve_input(argv: list[str]) -> str | None:
    """Find a label file from CLI args or the clipboard."""
    if len(argv) > 1:
        path = argv[1].strip().strip('"')
        if os.path.isfile(path):
            return path
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

    input_path = _resolve_input(sys.argv)

    mutex = _acquire_mutex()
    if mutex is None:
        log.info("Already running -- handing off %r and exiting.", input_path)
        _hand_off(input_path)
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

    from printpal.ui import MainWindow
    window = MainWindow(config, initial_path=input_path)
    _install_handoff_watch(window)
    _install_autoprint(window, config, log)
    log.info("Main window opened (initial file: %s)", input_path)
    window.run()


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
