"""PDF and image loading -- a thin wrapper around PyMuPDF.

`rasterize_pdf_region` is the core of the two-pass render: detect on a cheap
low-DPI page, then re-render *only* the label's box at print DPI. PyMuPDF's clip
is applied in the page's rotated coordinate space, so this stays correct even
for the /Rotate 90 sheets that FedEx and Amazon return labels ship as.
"""
from __future__ import annotations

from PIL import Image
import pymupdf

_SUPPORTED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")


def is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def page_count(path: str) -> int:
    """Number of pages: PDF page count, or 1 for a single image file."""
    if not is_pdf(path):
        return 1
    doc = pymupdf.open(path)
    try:
        return len(doc)
    finally:
        doc.close()


def _pixmap_to_image(pix) -> Image.Image:
    mode = "RGBA" if pix.alpha else "RGB"
    img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
    return img.convert("RGB") if mode == "RGBA" else img


def rasterize_pdf(path: str, dpi: int = 200, page: int = 0) -> Image.Image:
    """Render one page of a PDF to an RGB PIL Image."""
    doc = pymupdf.open(path)
    try:
        if page >= len(doc):
            raise ValueError(f"Page {page} does not exist (file has {len(doc)} pages)")
        pix = doc[page].get_pixmap(dpi=dpi)
        return _pixmap_to_image(pix)
    finally:
        doc.close()


def rasterize_pdf_region(path: str, clip_box: tuple[int, int, int, int],
                         detect_dpi: int, output_dpi: int,
                         page: int = 0) -> Image.Image:
    """Render only a region of a PDF page at output_dpi.

    `clip_box` is (x0, y0, x1, y1) in *detect_dpi* pixel coordinates. Converts to
    PDF points and clips so we never rasterize a whole Letter/A4 sheet at high
    DPI just to keep a 4x6 corner -- the speed win on slow machines.
    """
    doc = pymupdf.open(path)
    try:
        if page >= len(doc):
            raise ValueError(f"Page {page} does not exist (file has {len(doc)} pages)")
        scale = 72.0 / detect_dpi
        clip = pymupdf.Rect(
            clip_box[0] * scale, clip_box[1] * scale,
            clip_box[2] * scale, clip_box[3] * scale,
        )
        pix = doc[page].get_pixmap(dpi=output_dpi, clip=clip)
        return _pixmap_to_image(pix)
    finally:
        doc.close()


def load_image(path: str, dpi: int = 200, page: int = 0) -> Image.Image:
    """Load a PDF page or an image file as an RGB PIL Image."""
    if is_pdf(path):
        return rasterize_pdf(path, dpi, page)
    return Image.open(path).convert("RGB")
