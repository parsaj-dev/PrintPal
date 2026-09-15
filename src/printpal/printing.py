"""Send a label image to a Windows printer via the GDI print spooler.

Designed for thermal label printers (DYMO LabelWriter 450 and similar).
On non-Windows, provides stubs that raise NotImplementedError.
"""
from __future__ import annotations

import sys
from PIL import Image

if sys.platform == "win32":
    import win32print
    import win32ui
    import win32con

    def list_printers() -> list[str]:
        """Return names of all installed printers."""
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = win32print.EnumPrinters(flags, None, 1)
        return [p[2] for p in printers]

    def printer_exists(name: str) -> bool:
        return name in list_printers()

    def print_label(image: Image.Image, printer_name: str) -> None:
        """Send an image to the named printer, scaled to fit the page."""
        if not printer_exists(printer_name):
            raise RuntimeError(f'Printer "{printer_name}" not found. Available: {", ".join(list_printers())}')

        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer_name)

        try:
            hdc.StartDoc("PrintPal Label")
            hdc.StartPage()

            # printer page area in device units
            pw = hdc.GetDeviceCaps(win32con.HORZRES)
            ph = hdc.GetDeviceCaps(win32con.VERTRES)

            # scale image to fit, preserving aspect ratio
            iw, ih = image.size
            scale = min(pw / iw, ph / ih)
            dw = int(iw * scale)
            dh = int(ih * scale)

            # center on the label
            ox = (pw - dw) // 2
            oy = (ph - dh) // 2

            bmp_img = image.convert("RGB")
            dib = bmp_img.tobytes("raw", "BGR")

            # use StretchDIBits to draw the image
            import struct
            bi_size = 40
            bmp_header = struct.pack(
                "<IiiHHIIiiII",
                bi_size,        # biSize
                iw,             # biWidth
                -ih,            # biHeight (negative = top-down)
                1,              # biPlanes
                24,             # biBitCount
                0,              # biCompression (BI_RGB)
                0,              # biSizeImage
                0, 0,           # biXPelsPerMeter, biYPelsPerMeter
                0, 0,           # biClrUsed, biClrImportant
            )

            # pad each row to 4-byte alignment
            row_bytes = iw * 3
            pad = (4 - row_bytes % 4) % 4
            if pad:
                padded = b""
                for r in range(ih):
                    start = r * row_bytes
                    padded += dib[start:start + row_bytes] + b"\x00" * pad
                dib = padded

            ctypes_import = __import__("ctypes")
            gdi32 = ctypes_import.windll.gdi32
            gdi32.StretchDIBits(
                hdc.GetSafeHdc(),
                ox, oy, dw, dh,    # dest
                0, 0, iw, ih,      # src
                dib,
                bmp_header,
                0,                  # DIB_RGB_COLORS
                0x00CC0020,         # SRCCOPY
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
        return ["(not on Windows)"]

    def printer_exists(name: str) -> bool:
        return False

    def print_label(image: Image.Image, printer_name: str) -> None:
        raise NotImplementedError("Printing is only supported on Windows.")
