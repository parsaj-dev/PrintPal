"""PrintPal entry point.

Resolves the input file (CLI arg > clipboard > error), detects and crops the
label, and either auto-prints (high confidence) or shows a preview window.
Guarded against double invocation with a named mutex on Windows.
"""
from __future__ import annotations

import os
import sys
import traceback

from printpal.config import Config
from printpal.log import get_logger


def _acquire_mutex() -> object | None:
    """Try to grab a system-wide named mutex. Returns the handle if acquired, None if
    another instance already holds it. No-op on non-Windows."""
    if sys.platform != "win32":
        return True
    import win32event
    import win32api
    import winerror
    mutex = win32event.CreateMutex(None, False, "Global\\PrintPalSingleInstance")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        win32api.CloseHandle(mutex)
        return None
    return mutex


def _release_mutex(handle) -> None:
    if handle is None or handle is True:
        return
    if sys.platform == "win32":
        import win32api
        win32api.CloseHandle(handle)


def _resolve_input(argv: list[str]) -> str | None:
    """Find the PDF/image path from CLI args or clipboard."""
    # 1. CLI argument
    if len(argv) > 1:
        path = argv[1].strip().strip('"')
        if os.path.isfile(path):
            return path

    # 2. Clipboard
    from printpal.clipboard import get_pdf_path
    return get_pdf_path()


def _validate_file(path: str) -> str | None:
    """Return an error message if the file is not a supported type, else None."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
        return f"Unsupported file type: {ext}. PrintPal works with PDF, PNG, and JPEG files."
    # quick sanity check: PDF files should start with %PDF
    if ext == ".pdf":
        try:
            with open(path, "rb") as f:
                header = f.read(8)
            if not header.startswith(b"%PDF"):
                return "This file has a .pdf extension but does not look like a valid PDF."
        except OSError as e:
            return f"Cannot read file: {e}"
    return None


def main() -> None:
    log = get_logger()
    log.info("PrintPal started")

    mutex = _acquire_mutex()
    if mutex is None:
        log.info("Another instance is already running, exiting.")
        return

    try:
        _run(log)
    except Exception:
        log.error("Unhandled error:\n%s", traceback.format_exc())
        from printpal.ui import show_error
        show_error("PrintPal Error", "Something went wrong. Check the log file for details.")
    finally:
        _release_mutex(mutex)
        log.info("PrintPal exiting")


def _run(log) -> None:
    config = Config.load()
    log.info("Config loaded: printer=%s, dpi=%d", config.printer, config.dpi)

    # verify the configured printer exists, offer a picker if not
    from printpal.printing import printer_exists, list_printers
    if not printer_exists(config.printer):
        available = list_printers()
        log.warning("Printer %r not found. Available: %s", config.printer, available)
        from printpal.ui import PrinterPicker
        picker = PrinterPicker(
            available, config.printer,
            f'Printer "{config.printer}" was not found.\n\n'
            f"Pick one of the {len(available)} printers installed on this machine. "
            f"This will be saved as your default."
        )
        chosen = picker.run()
        if chosen is None:
            log.info("User cancelled printer selection.")
            return
        config.printer = chosen
        config.save()
        log.info("Printer set to: %s", config.printer)

    path = _resolve_input(sys.argv)
    if path is None:
        log.info("No input file found on clipboard or command line.")
        from printpal.ui import show_info
        # show the log path so the user can check what happened
        from printpal.config import _CONFIG_DIR
        log_path = _CONFIG_DIR / "logs" / "printpal.log"
        show_info(
            "PrintPal",
            "Copy a label PDF first, then click the app.\n\n"
            "Accepted inputs:\n"
            "- Copy a PDF file in Explorer (right-click > Copy)\n"
            "- Copy a file path (Ctrl+Shift+C in Explorer)\n"
            "- Copy a file:/// URL from Chrome's address bar\n\n"
            f"If this keeps happening, check the log:\n{log_path}"
        )
        return

    log.info("Input file: %s (%d bytes)", path, os.path.getsize(path))

    err = _validate_file(path)
    if err:
        log.warning("Validation failed: %s", err)
        from printpal.ui import show_error
        show_error("PrintPal", err)
        return

    # two-pass: detect at low DPI (fast), then crop at high DPI (quality)
    from printpal.rasterize import load_image
    from printpal.detect import find_label, crop_at_dpi

    DETECT_DPI = 200
    PRINT_DPI = 600

    log.info("Rasterizing at %d DPI for detection...", DETECT_DPI)
    img = load_image(path, dpi=DETECT_DPI)
    log.info("Page size: %dx%d px", img.width, img.height)

    result = find_label(img, dpi=DETECT_DPI)
    log.info("Detection: method=%s, confidence=%.2f, barcodes_in=%d, barcodes_out=%d, box=%s",
             result.method, result.confidence, result.barcodes_in, result.barcodes_out, result.box)
    for w in result.warnings:
        log.warning("Detection warning: %s", w)

    # re-rasterize at print DPI and crop for full quality output
    print_image = result.image  # fallback to detection-quality if not PDF
    if path.lower().endswith(".pdf") and result.confidence > 0:
        log.info("Re-rasterizing at %d DPI for print quality...", PRINT_DPI)
        img_hq = load_image(path, dpi=PRINT_DPI)
        log.info("High-quality page: %dx%d px", img_hq.width, img_hq.height)
        print_image = crop_at_dpi(img_hq, result, PRINT_DPI)
        log.info("Print-quality crop: %dx%d px", print_image.width, print_image.height)

    def do_print():
        log.info("Sending to printer: %s", config.printer)
        try:
            from printpal.printing import print_label
            print_label(print_image, config.printer)
            log.info("Print job submitted.")
        except Exception as e:
            log.error("Print failed: %s", e)
            from printpal.ui import show_error
            show_error("PrintPal - Print Error", str(e))

    def do_cancel():
        log.info("User cancelled.")

    if result.confidence >= 0.7:
        # high confidence: auto-print, show a brief notification
        log.info("High confidence (%.2f), auto-printing.", result.confidence)
        do_print()
        # quick notification instead of a blocking window
        from printpal.ui import show_info
        show_info("PrintPal", f"Label sent to {config.printer}.")
    else:
        # low confidence: show preview
        log.info("Low confidence (%.2f), showing preview.", result.confidence)
        from printpal.ui import PreviewWindow
        preview = PreviewWindow(result, config, on_print=do_print, on_cancel=do_cancel)
        preview.run()


if __name__ == "__main__":
    main()
