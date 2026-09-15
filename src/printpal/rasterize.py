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


def load_image(path: str, dpi: int = 200) -> Image.Image:
    """Load a PDF page or image file as an RGB PIL Image."""
    if path.lower().endswith(".pdf"):
        return rasterize_pdf(path, dpi)
    return Image.open(path).convert("RGB")
