"""Write PIL page images into a minimal OpenXPS (.xps) package.

This mirrors what a Windows XPS print driver emits when you File > Print to an
XPS-based virtual printer: a fixed-layout document whose pages carry raster
content. It lets the tests exercise the exact same code path the "PrintPal"
virtual printer feeds -- an .xps document read back through PyMuPDF -- without a
Windows box or a print driver.

Not used at runtime by the app; it is a dev/test helper (and a reference for the
winprinter component). Depends only on Pillow + the stdlib.
"""
from __future__ import annotations

import io
import zipfile
from typing import Iterable

from PIL import Image

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="fdseq" ContentType="application/vnd.ms-package.xps-fixeddocumentsequence+xml"/>'
    '<Default Extension="fdoc" ContentType="application/vnd.ms-package.xps-fixeddocument+xml"/>'
    '<Default Extension="fpage" ContentType="application/vnd.ms-package.xps-fixedpage+xml"/>'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '</Types>'
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.microsoft.com/xps/2005/06/fixedrepresentation" '
    'Target="/FixedDocumentSequence.fdseq"/>'
    '</Relationships>'
)

_XPS_NS = "http://schemas.microsoft.com/xps/2005/06"


def pil_pages_to_xps(pages: Iterable[Image.Image], out_path: str, dpi: int = 200) -> str:
    """Pack ``pages`` (PIL images) into an OpenXPS document at ``out_path``.

    Each page's physical size is derived from its pixel size and ``dpi`` (so a
    1200x1800 image at 200 dpi becomes a 6x9in page; a 800x1200 image a 4x6in
    label). Returns ``out_path``.
    """
    pages = list(pages)
    fdoc_refs = []
    parts: dict[str, bytes] = {}

    for i, img in enumerate(pages, start=1):
        rgb = img.convert("RGB")
        png = io.BytesIO()
        rgb.save(png, format="PNG")
        img_part = f"Documents/1/Resources/Images/{i}.png"
        parts[img_part] = png.getvalue()

        # XPS coordinates are in units of 1/96 inch.
        w96 = rgb.width * 96.0 / dpi
        h96 = rgb.height * 96.0 / dpi
        fpage = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<FixedPage xmlns="{_XPS_NS}" Width="{w96:.2f}" Height="{h96:.2f}" xml:lang="en-US">'
            f'<Path Data="M 0,0 L {w96:.2f},0 {w96:.2f},{h96:.2f} 0,{h96:.2f} Z">'
            '<Path.Fill>'
            f'<ImageBrush ImageSource="/{img_part}" '
            f'Viewbox="0,0,{rgb.width},{rgb.height}" ViewboxUnits="Absolute" '
            f'Viewport="0,0,{w96:.2f},{h96:.2f}" ViewportUnits="Absolute" TileMode="None"/>'
            '</Path.Fill></Path></FixedPage>'
        )
        page_part = f"Documents/1/Pages/{i}.fpage"
        parts[page_part] = fpage.encode("utf-8")
        fdoc_refs.append(f'<PageContent Source="Pages/{i}.fpage"/>')

    parts["[Content_Types].xml"] = _CONTENT_TYPES.encode("utf-8")
    parts["_rels/.rels"] = _RELS.encode("utf-8")
    parts["FixedDocumentSequence.fdseq"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<FixedDocumentSequence xmlns="{_XPS_NS}">'
        '<DocumentReference Source="Documents/1/FixedDocument.fdoc"/>'
        '</FixedDocumentSequence>'
    ).encode("utf-8")
    parts["Documents/1/FixedDocument.fdoc"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<FixedDocument xmlns="{_XPS_NS}">' + "".join(fdoc_refs) + '</FixedDocument>'
    ).encode("utf-8")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return out_path
