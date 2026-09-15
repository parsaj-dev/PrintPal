"""PDF and image loading. Thin wrapper around PyMuPDF."""
from __future__ import annotations

from PIL import Image
import pymupdf


def rasterize_pdf(path: str, dpi: int = 200, page: int = 0) -> Image.Image:
    """Render one page of a PDF to an RGB PIL Image."""
    doc = pymupdf.open(path)
    if page >= len(doc):
        raise ValueError(f"Page {page} does not exist (file has {len(doc)} pages)")
    pix = doc[page].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def rasterize_pdf_region(path: str, clip_box: tuple[int, int, int, int],
                         detect_dpi: int, output_dpi: int,
                         page: int = 0) -> Image.Image:
    """Render only a region of a PDF page at output_dpi.

    clip_box is (x0, y0, x1, y1) in detect_dpi pixel coordinates.
    Converts to PDF points and uses PyMuPDF's clip to avoid rasterizing
    the entire page at high DPI.
    """
    doc = pymupdf.open(path)
    if page >= len(doc):
        raise ValueError(f"Page {page} does not exist (file has {len(doc)} pages)")
    # convert pixel coords at detect_dpi to PDF points (72 dpi)
    scale = 72.0 / detect_dpi
    clip = pymupdf.Rect(
        clip_box[0] * scale, clip_box[1] * scale,
        clip_box[2] * scale, clip_box[3] * scale,
    )
    pix = doc[page].get_pixmap(dpi=output_dpi, clip=clip)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def load_image(path: str, dpi: int = 200) -> Image.Image:
    """Load a PDF page or image file as an RGB PIL Image."""
    if path.lower().endswith(".pdf"):
        return rasterize_pdf(path, dpi)
    return Image.open(path).convert("RGB")
