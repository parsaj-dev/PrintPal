"""Send a label image to a Windows printer through the GDI spooler.

Built for thermal label printers (DYMO LabelWriter, Zebra, Rollo, ...). The
quality trick: resize the label to the printer's exact device pixels with a good
Lanczos filter in Pillow and blit it 1:1, instead of letting GDI's nearest-
neighbour stretch chew up barcode bars and thin text. On non-Windows we expose
stubs so the rest of the app imports and unit-tests cleanly.
"""
from __future__ import annotations

import struct
import sys

from PIL import Image

# Print at most this many device pixels per side, a safety cap against a
# pathological media size that would allocate an enormous DIB.
_MAX_DEVICE_PX = 6000


def prepare_for_page(image: Image.Image, page_w: int, page_h: int,
                     fit_rotate: bool = True) -> tuple[Image.Image, int, int]:
    """Scale `image` to fit a (page_w x page_h) device rectangle, preserving
    aspect ratio, and return (resized_rgb_image, offset_x, offset_y).

    When `fit_rotate` is set and the label is clearly cross-oriented to the media
    (portrait label on landscape media or vice versa), it is turned 90 degrees so
    it fills the media instead of printing small in the middle.
    """
    if page_w <= 0 or page_h <= 0:
        return image.convert("RGB"), 0, 0

    iw, ih = image.size

    def fit_scale(w, h):
        return min(page_w / w, page_h / h)

    if fit_rotate and iw > 0 and ih > 0:
        label_landscape = iw > ih
        page_landscape = page_w > page_h
        if label_landscape != page_landscape:
            # Rotating aligns the long axes; take it if it prints meaningfully bigger.
            if fit_scale(ih, iw) > fit_scale(iw, ih) * 1.02:
                image = image.transpose(Image.ROTATE_90)
                iw, ih = image.size

    scale = fit_scale(iw, ih)
    dw = max(1, min(_MAX_DEVICE_PX, int(round(iw * scale))))
    dh = max(1, min(_MAX_DEVICE_PX, int(round(ih * scale))))

    resized = image.convert("RGB").resize((dw, dh), Image.LANCZOS)
    ox = max(0, (page_w - dw) // 2)
    oy = max(0, (page_h - dh) // 2)
    return resized, ox, oy


def _dib_bytes(image: Image.Image) -> tuple[bytes, bytes, int, int]:
    """Return (pixel_bytes, BITMAPINFOHEADER, width, height) for a top-down
    24-bit DIB with 4-byte aligned rows -- the shape StretchDIBits wants."""
    rgb = image.convert("RGB")
    iw, ih = rgb.size
    raw = rgb.tobytes("raw", "BGR")

    row_bytes = iw * 3
    pad = (4 - row_bytes % 4) % 4
    if pad:
        rows = [raw[r * row_bytes:(r + 1) * row_bytes] + b"\x00" * pad for r in range(ih)]
        raw = b"".join(rows)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        iw,          # biWidth
        -ih,         # biHeight (negative -> top-down rows)
        1,           # biPlanes
        24,          # biBitCount
        0,           # biCompression (BI_RGB)
        0,           # biSizeImage
        0, 0,        # pixels-per-meter x/y
        0, 0,        # biClrUsed, biClrImportant
    )
    return raw, header, iw, ih


if sys.platform == "win32":
    import ctypes
    import win32con
    import win32print
    import win32ui

    _HALFTONE = 4  # win32con.HALFTONE is not always exposed

    def list_printers() -> list[str]:
        """Names of all installed printers, default first when discoverable."""
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = [p[2] for p in win32print.EnumPrinters(flags, None, 1)]
        try:
            default = win32print.GetDefaultPrinter()
            if default in names:
                names.remove(default)
                names.insert(0, default)
        except Exception:
            pass
        return names

    def printer_exists(name: str) -> bool:
        return name in list_printers()

    def default_printer() -> str | None:
        try:
            return win32print.GetDefaultPrinter()
        except Exception:
            return None

    def print_label(image: Image.Image, printer_name: str, copies: int = 1,
                    fit_rotate: bool = True) -> None:
        """Send an image to the named printer, scaled to fill the media."""
        if not printer_exists(printer_name):
            raise RuntimeError(
                f'Printer "{printer_name}" not found. '
                f'Available: {", ".join(list_printers()) or "none"}')

        copies = max(1, min(99, int(copies)))

        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer_name)
        raw_hdc = hdc.GetSafeHdc()
        gdi32 = ctypes.windll.gdi32
        try:
            page_w = hdc.GetDeviceCaps(win32con.HORZRES)
            page_h = hdc.GetDeviceCaps(win32con.VERTRES)

            resized, ox, oy = prepare_for_page(image, page_w, page_h, fit_rotate)
            raw, header, iw, ih = _dib_bytes(resized)

            gdi32.SetStretchBltMode(raw_hdc, _HALFTONE)
            gdi32.SetBrushOrgEx(raw_hdc, 0, 0, None)

            for _ in range(copies):
                hdc.StartDoc("PrintPal Label")
                hdc.StartPage()
                gdi32.StretchDIBits(
                    raw_hdc,
                    ox, oy, iw, ih,     # destination (1:1 with the resized image)
                    0, 0, iw, ih,       # source
                    raw, header,
                    win32con.DIB_RGB_COLORS,
                    win32con.SRCCOPY,
                )
                hdc.EndPage()
                hdc.EndDoc()
        except Exception:
            try:
                hdc.AbortDoc()
            except Exception:
                pass
            raise
        finally:
            hdc.DeleteDC()

else:
    def list_printers() -> list[str]:
        return ["(printing is only available on Windows)"]

    def printer_exists(name: str) -> bool:
        return False

    def default_printer() -> str | None:
        return None

    def print_label(image: Image.Image, printer_name: str, copies: int = 1,
                    fit_rotate: bool = True) -> None:
        raise NotImplementedError("Printing is only supported on Windows.")
