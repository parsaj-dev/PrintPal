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

# Multi-page, vector documents MuPDF renders at a chosen DPI. XPS/OXPS are here
# because the "PrintPal" virtual printer emits XPS -- MuPDF reads it natively, so
# a printed job flows through the exact same engine as a dropped PDF, with no
# Ghostscript (and no AGPL) anywhere in the picture.
_DOCUMENT_EXT = (".pdf", ".xps", ".oxps")

# When an image file carries no usable resolution metadata we can't know its true
# physical size. We assume it was produced at a thermal label printer's native
# resolution (203 dpi is typical for Zebra/Rollo/DYMO), which keeps a bare-label
# image in the "label media" regime and gives a sensible inches read-out.
DEFAULT_IMAGE_DPI = 203


def is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def is_document(path: str) -> bool:
    """True for a page-based vector document MuPDF renders (PDF, XPS, OXPS)."""
    return path.lower().endswith(_DOCUMENT_EXT)


def image_dpi(path: str, img: Image.Image | None = None) -> int:
    """Best-effort resolution (pixels per inch) of an image file.

    Reads the file's DPI metadata (PNG pHYs, JPEG/TIFF resolution tags) and falls
    back to ``DEFAULT_IMAGE_DPI`` when it is missing or implausible. PDFs are
    rendered at a chosen DPI, so this is only meaningful for image inputs.
    """
    dpi = None
    try:
        if img is not None:
            dpi = img.info.get("dpi")
        else:
            with Image.open(path) as im:
                dpi = im.info.get("dpi")
    except Exception:
        dpi = None
    if dpi:
        val = dpi[0] if isinstance(dpi, (tuple, list)) else dpi
        try:
            val = float(val)
        except (TypeError, ValueError):
            val = 0.0
        # Some encoders write a bogus 1 dpi or 0 dpi; ignore anything sub-photo.
        if val >= 72:
            return int(round(val))
    return DEFAULT_IMAGE_DPI


def page_count(path: str) -> int:
    """Number of pages: document page count, or 1 for a single image file."""
    if not is_document(path):
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


def iter_pages(path: str, dpi: int = 200, limit: int | None = None):
    """Yield (page_index, page_count, image) for every page, opening and parsing
    the document ONCE instead of once per page (a real cost on multi-page PDFs
    and slow disks). Image files yield a single page."""
    if not is_document(path):
        yield 0, 1, Image.open(path).convert("RGB")
        return
    doc = pymupdf.open(path)
    try:
        total = len(doc)
        for i in range(min(total, limit) if limit else total):
            yield i, total, _pixmap_to_image(doc[i].get_pixmap(dpi=dpi))
    finally:
        doc.close()


def load_image(path: str, dpi: int = 200, page: int = 0) -> Image.Image:
    """Load a document page (PDF/XPS/OXPS) or an image file as an RGB PIL Image."""
    if is_document(path):
        return rasterize_pdf(path, dpi, page)
    return Image.open(path).convert("RGB")
