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
from dataclasses import dataclass

from PIL import Image

# Print at most this many device pixels per side, a safety cap against a
# pathological media size that would allocate an enormous DIB.
_MAX_DEVICE_PX = 6000


# Print layout modes: scale to fill the media, a percentage of the label's
# actual size, or fit inside a box of a given size in inches.
MODE_FIT = "fit"
MODE_PERCENT = "percent"
MODE_SIZE = "size"
MODES = (MODE_FIT, MODE_PERCENT, MODE_SIZE)

# Where the label sits on the media when it doesn't fill it, as
# (horizontal, vertical) fractions of the free space.
ANCHORS = {
    "top-left": (0.0, 0.0), "top": (0.5, 0.0), "top-right": (1.0, 0.0),
    "left": (0.0, 0.5), "center": (0.5, 0.5), "right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0), "bottom": (0.5, 1.0), "bottom-right": (1.0, 1.0),
}

# Assumed resolution of an image that doesn't say (only used for actual-size
# printing: "fit" doesn't care).
_FALLBACK_IMAGE_DPI = 300


@dataclass(frozen=True)
class PrintLayout:
    """How a label is sized and placed on the printer's page.

    ``width_in`` / ``height_in`` are as the label lands on the paper (after any
    auto-rotation). With ``keep_aspect`` the label fits inside that box;
    without it, it is stretched to exactly that size. ``offset_*_in`` nudge it
    from its anchor (positive = right / down)."""
    mode: str = MODE_FIT
    percent: float = 100.0
    width_in: float = 4.0
    height_in: float = 6.0
    keep_aspect: bool = True
    anchor: str = "center"
    offset_x_in: float = 0.0
    offset_y_in: float = 0.0

    @classmethod
    def from_config(cls, config) -> "PrintLayout":
        return cls(mode=config.scale_mode, percent=config.scale_percent,
                   width_in=config.scale_width_in, height_in=config.scale_height_in,
                   keep_aspect=config.scale_keep_aspect, anchor=config.position,
                   offset_x_in=config.offset_x_in, offset_y_in=config.offset_y_in)

    def describe(self) -> str:
        """Short button text, e.g. "Fit", "85%", "4 × 6 in"."""
        if self.mode == MODE_PERCENT:
            return f"{self.percent:g}%"
        if self.mode == MODE_SIZE:
            return f"{self.width_in:g} × {self.height_in:g} in"
        return "Fit"


@dataclass(frozen=True)
class Placement:
    """Where a label lands on the page, in page units: rotated 90 degrees or
    not, and its (possibly off-page) rectangle."""
    rotated: bool
    x: float
    y: float
    w: float
    h: float


def place(iw: float, ih: float, image_dpi: float, page_w: float, page_h: float,
          dpi_x: float, dpi_y: float, layout: PrintLayout | None = None,
          fit_rotate: bool = True) -> Placement:
    """Lay an ``iw`` x ``ih`` image (``image_dpi``) out on a ``page_w`` x
    ``page_h`` page whose units are ``dpi_x`` / ``dpi_y`` per inch.

    Pure arithmetic shared by printing and the on-screen layout preview. The
    result may extend past the page (a big percentage, a nudge); the caller
    clips."""
    layout = layout or PrintLayout()
    rotated = False
    if fit_rotate and iw > 0 and ih > 0 and page_w > 0 and page_h > 0:
        if (iw > ih) != (page_w > page_h):
            # Rotating aligns the long axes; take it if it prints meaningfully bigger.
            fit = lambda w, h: min(page_w / w, page_h / h)  # noqa: E731
            if fit(ih, iw) > fit(iw, ih) * 1.02:
                rotated = True
                iw, ih = ih, iw
    iw, ih = max(1e-6, iw), max(1e-6, ih)

    if layout.mode == MODE_PERCENT:
        k = max(1.0, min(1000.0, layout.percent)) / 100.0 / max(1e-6, image_dpi)
        w, h = iw * k * dpi_x, ih * k * dpi_y
    elif layout.mode == MODE_SIZE:
        bw = max(0.1, layout.width_in) * dpi_x
        bh = max(0.1, layout.height_in) * dpi_y
        if layout.keep_aspect:
            s = min(bw / iw, bh / ih)
            w, h = iw * s, ih * s
        else:
            w, h = bw, bh
    else:
        s = min(page_w / iw, page_h / ih)
        w, h = iw * s, ih * s

    ax, ay = ANCHORS.get(layout.anchor, ANCHORS["center"])
    x = (page_w - w) * ax + layout.offset_x_in * dpi_x
    y = (page_h - h) * ay + layout.offset_y_in * dpi_y
    return Placement(rotated, x, y, w, h)


def image_dpi_of(image: Image.Image) -> float:
    """The resolution a print image was rendered at (``info["dpi"]``)."""
    dpi = image.info.get("dpi")
    try:
        value = float(dpi[0] if isinstance(dpi, (tuple, list)) else dpi)
    except (TypeError, ValueError, IndexError):
        return _FALLBACK_IMAGE_DPI
    return value if value > 1 else _FALLBACK_IMAGE_DPI


@dataclass
class DevicePlacement:
    """A label ready to blit: ``image`` drawn into the device rectangle
    (x, y, w, h). The image is normally exactly w x h (a 1:1 blit); it is only
    smaller when the device rectangle exceeds the safety cap."""
    image: Image.Image
    x: int
    y: int
    w: int
    h: int


def place_on_page(image: Image.Image, page_w: int, page_h: int,
                  fit_rotate: bool = True, layout: PrintLayout | None = None,
                  device_dpi: tuple[float, float] = (300.0, 300.0)) -> DevicePlacement:
    """Size, rotate and position ``image`` on a (page_w x page_h) device page.

    Only the part of the label that lands on the page is resampled, so a label
    blown up past the page edge costs no more than one that fits."""
    if page_w <= 0 or page_h <= 0:
        rgb = image.convert("RGB")
        return DevicePlacement(rgb, 0, 0, rgb.width, rgb.height)

    dpi_x, dpi_y = device_dpi
    p = place(image.width, image.height, image_dpi_of(image), page_w, page_h,
              dpi_x, dpi_y, layout, fit_rotate)
    if p.rotated:
        image = image.transpose(Image.ROTATE_90)
    iw, ih = image.size

    # Clip the label's device rectangle to the page.
    vx0, vy0 = max(0.0, p.x), max(0.0, p.y)
    vx1, vy1 = min(float(page_w), p.x + p.w), min(float(page_h), p.y + p.h)
    if vx1 - vx0 < 1 or vy1 - vy0 < 1:
        raise ValueError("The label is positioned entirely off the page -- "
                         "check the size and position settings.")
    dx0, dy0 = int(round(vx0)), int(round(vy0))
    dw, dh = max(1, int(round(vx1)) - dx0), max(1, int(round(vy1)) - dy0)

    # The matching part of the source image.
    sx, sy = iw / p.w, ih / p.h
    src = ((vx0 - p.x) * sx, (vy0 - p.y) * sy, (vx1 - p.x) * sx, (vy1 - p.y) * sy)

    # Cap the bitmap uniformly (keeps the aspect); GDI stretches the rest.
    cap = min(1.0, _MAX_DEVICE_PX / max(dw, dh))
    out = (max(1, int(round(dw * cap))), max(1, int(round(dh * cap))))
    rgb = image.convert("RGB")
    full = src == (0.0, 0.0, float(iw), float(ih))
    resized = rgb.resize(out, Image.LANCZOS, box=None if full else src)
    return DevicePlacement(resized, dx0, dy0, dw, dh)


def prepare_for_page(image: Image.Image, page_w: int, page_h: int,
                     fit_rotate: bool = True, layout: PrintLayout | None = None,
                     device_dpi: tuple[float, float] = (300.0, 300.0)
                     ) -> tuple[Image.Image, int, int]:
    """Scale `image` onto a (page_w x page_h) device rectangle and return
    (resized_rgb_image, offset_x, offset_y). By default it fills the page,
    preserving aspect ratio, centred.

    When `fit_rotate` is set and the label is clearly cross-oriented to the media
    (portrait label on landscape media or vice versa), it is turned 90 degrees so
    it fills the media instead of printing small in the middle.
    """
    pl = place_on_page(image, page_w, page_h, fit_rotate, layout, device_dpi)
    return pl.image, pl.x, pl.y


def _dib_bytes(image: Image.Image) -> tuple[bytes, bytes, int, int]:
    """Return (pixel_bytes, BITMAPINFOHEADER, width, height) for a top-down
    24-bit DIB with 4-byte aligned rows -- the shape StretchDIBits wants."""
    rgb = image.convert("RGB")
    iw, ih = rgb.size
    row_bytes = iw * 3
    stride = row_bytes + (4 - row_bytes % 4) % 4
    # Pillow's raw encoder writes each row at `stride` bytes -- the 4-byte
    # alignment a DIB needs -- in C, instead of re-slicing rows in Python.
    raw = rgb.tobytes("raw", "BGR", stride)

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
        # Open the one printer by name instead of enumerating them all: on an
        # office PC with mapped network printers EnumPrinters can take seconds.
        if not name:
            return False
        try:
            handle = win32print.OpenPrinter(name)
        except Exception:
            return False
        try:
            win32print.ClosePrinter(handle)
        except Exception:
            pass
        return True

    def default_printer() -> str | None:
        try:
            return win32print.GetDefaultPrinter()
        except Exception:
            return None

    def page_size_inches(name: str) -> tuple[float, float] | None:
        """The printable area of ``name``'s current page, in inches, or None if
        the printer can't be opened. Used for the layout preview."""
        try:
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(name)
        except Exception:
            return None
        try:
            w = hdc.GetDeviceCaps(win32con.HORZRES) / max(1, hdc.GetDeviceCaps(win32con.LOGPIXELSX))
            h = hdc.GetDeviceCaps(win32con.VERTRES) / max(1, hdc.GetDeviceCaps(win32con.LOGPIXELSY))
            return (w, h) if w > 0 and h > 0 else None
        except Exception:
            return None
        finally:
            try:
                hdc.DeleteDC()
            except Exception:
                pass

    def print_label(image: Image.Image, printer_name: str, copies: int = 1,
                    fit_rotate: bool = True, layout: PrintLayout | None = None) -> None:
        """Send an image to the named printer, sized and placed per ``layout``
        (default: scaled to fill the media)."""
        copies = max(1, min(99, int(copies)))

        hdc = win32ui.CreateDC()
        try:
            # No up-front printer enumeration (slow with network printers):
            # just try to open it, and only list printers if that fails.
            hdc.CreatePrinterDC(printer_name)
        except Exception:
            raise RuntimeError(
                f'Printer "{printer_name}" not found. '
                f'Available: {", ".join(list_printers()) or "none"}') from None
        raw_hdc = hdc.GetSafeHdc()
        gdi32 = ctypes.windll.gdi32
        try:
            page_w = hdc.GetDeviceCaps(win32con.HORZRES)
            page_h = hdc.GetDeviceCaps(win32con.VERTRES)

            dpi = (hdc.GetDeviceCaps(win32con.LOGPIXELSX) or 300,
                   hdc.GetDeviceCaps(win32con.LOGPIXELSY) or 300)
            pl = place_on_page(image, page_w, page_h, fit_rotate, layout, dpi)
            raw, header, iw, ih = _dib_bytes(pl.image)

            gdi32.SetStretchBltMode(raw_hdc, _HALFTONE)
            gdi32.SetBrushOrgEx(raw_hdc, 0, 0, None)

            # One spooler document with N identical pages, not N separate jobs --
            # so the queue shows a single "PrintPal Label" entry and the copies
            # stay together on label stock.
            hdc.StartDoc("PrintPal Label")
            for _ in range(copies):
                hdc.StartPage()
                gdi32.StretchDIBits(
                    raw_hdc,
                    pl.x, pl.y, pl.w, pl.h,   # destination (normally 1:1 with the image)
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

    def page_size_inches(name: str) -> tuple[float, float] | None:
        return None

    def print_label(image: Image.Image, printer_name: str, copies: int = 1,
                    fit_rotate: bool = True, layout: PrintLayout | None = None) -> None:
        raise NotImplementedError("Printing is only supported on Windows.")
