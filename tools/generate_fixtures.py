"""Generate synthetic shipping-label fixtures with real, decodable barcodes.

Used by the integration tests and for eyeballing detection/printing by hand. The
labels are drawn from scratch (no real customer data) and carry genuine Code128
barcodes so the whole detect -> decode -> crop path can be exercised headless.

    python tools/generate_fixtures.py out_dir/

Produces:
    bare_4x6.pdf      a bare 4x6 label (label media)
    letter_sheet.pdf  a Letter sheet: label block + instructions (document media)
    twoup.pdf         one page holding 2 labels (Etsy/Amazon N-up style)
    fourup.pdf        one page holding 4 labels
    multi.pdf         a 3-page PDF: label, packing slip, label

Requires `python-barcode` (a dev-only dependency): pip install -e ".[dev]".
"""
from __future__ import annotations

import io
import os
import sys

import barcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont
import pymupdf

DPI = int(os.environ.get("FIXTURE_DPI", "200"))


def _font(size: int, bold: bool = False):
    names = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _code128(data: str) -> Image.Image:
    cls = barcode.get_barcode_class("code128")
    obj = cls(data, writer=ImageWriter())
    buf = io.BytesIO()
    obj.write(buf, options={
        "module_width": 0.5, "module_height": 34.0, "quiet_zone": 2.0,
        "font_size": 8, "text_distance": 3.0, "dpi": DPI,
    })
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def make_label_image(w_in: float = 4, h_in: float = 6,
                     tracking: str = "1Z999AA10123456784", carrier: str = "UPS") -> Image.Image:
    """Draw a realistic, densely-laid-out 4x6 shipping label."""
    W, H = int(w_in * DPI), int(h_in * DPI)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    m = int(0.12 * DPI)
    s = DPI / 300.0

    band_h = int(0.5 * DPI)
    d.rectangle([m, m, W - m, m + band_h], fill="black")
    d.text((m + int(15 * s), m + int(12 * s)), carrier, fill="white", font=_font(int(60 * s), True))
    d.text((W - m - int(200 * s), m + int(20 * s)), "GROUND", fill="white", font=_font(int(30 * s), True))

    y = m + band_h + int(0.05 * DPI)
    d.text((m, y), "SHIP FROM:", font=_font(int(20 * s), True), fill="black")
    d.text((m + int(150 * s), y), "PrintPal Test Sender, 123 Origin Rd, Springfield IL 62704",
           font=_font(int(18 * s)), fill="black")
    y += int(0.30 * DPI)
    d.line([m, y, W - m, y], fill="black", width=2)
    y += int(0.06 * DPI)
    d.text((m, y), "SHIP TO:", font=_font(int(26 * s), True), fill="black")
    d.text((m + int(20 * s), y + int(0.26 * DPI)),
           "Jane Recipient\n987 Delivery Ave, Apt 4\nAustin, TX 78701\nUS",
           font=_font(int(30 * s), True), fill="black")
    y += int(1.05 * DPI)

    d.line([m, y, W - m, y], fill="black", width=2)
    y += int(0.06 * DPI)
    box_h = int(0.7 * DPI)
    d.rectangle([m, y, W - m, y + box_h], outline="black", width=3)
    d.text((m + int(20 * s), y + int(14 * s)), "TX 787 9-01", font=_font(int(66 * s), True), fill="black")
    y += box_h + int(0.16 * DPI)

    bc = _code128(tracking)
    bw = W - 2 * m
    bh = int(bc.height * (bw / bc.width))
    bc = bc.resize((bw, bh), Image.LANCZOS)
    d.text((m, y), f"TRACKING #: {tracking}", font=_font(int(22 * s)), fill="black")
    y += int(0.20 * DPI)
    img.paste(bc, (m, min(y, H - m - bh)))
    return img


def _in(x: float) -> float:
    return x * 72.0


def _place(page, img: Image.Image, rect: pymupdf.Rect) -> None:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    page.insert_image(rect, stream=buf.getvalue())


def generate(out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []

    def save(doc, name):
        path = os.path.join(out_dir, name)
        doc.save(path)
        doc.close()
        written.append(path)

    # 1) Bare 4x6 label
    doc = pymupdf.open()
    page = doc.new_page(width=_in(4), height=_in(6))
    _place(page, make_label_image(), pymupdf.Rect(0, 0, _in(4), _in(6)))
    save(doc, "bare_4x6.pdf")

    # 2) Letter sheet: label top-left, instructions below
    doc = pymupdf.open()
    page = doc.new_page(width=_in(8.5), height=_in(11))
    _place(page, make_label_image(carrier="FEDEX", tracking="794658123456"),
           pymupdf.Rect(_in(0.5), _in(0.5), _in(4.5), _in(6.5)))
    page.insert_textbox(
        pymupdf.Rect(_in(0.7), _in(7.6), _in(8.0), _in(10.5)),
        "RETURN INSTRUCTIONS\n\n"
        "1. Pack the item securely in its original packaging.\n"
        "2. Affix this shipping label to the outside of the box.\n"
        "3. Drop off at any authorized location within 30 days.\n"
        "4. Keep the top portion of this page for your records.\n\n"
        "This label is valid only for the return authorization listed above.",
        fontsize=11, fontname="helv")
    save(doc, "letter_sheet.pdf")

    # 3) Two-up: two 4x6 labels on one 8x6 landscape page
    doc = pymupdf.open()
    page = doc.new_page(width=_in(8), height=_in(6))
    for i, trk in enumerate(["1Z111AA10111111111", "1Z222BB20222222222"]):
        _place(page, make_label_image(tracking=trk),
               pymupdf.Rect(i * _in(4), 0, i * _in(4) + _in(4), _in(6)))
    save(doc, "twoup.pdf")

    # 4) Four-up: four labels on a Letter sheet (2x2)
    doc = pymupdf.open()
    page = doc.new_page(width=_in(8.5), height=_in(11))
    for i, (x, y) in enumerate([(0.25, 0.25), (4.35, 0.25), (0.25, 5.5), (4.35, 5.5)]):
        _place(page, make_label_image(tracking=f"1Z{i}0{i}CC30{i}33333333"),
               pymupdf.Rect(_in(x), _in(y), _in(x + 3.9), _in(y + 5.2)))
    save(doc, "fourup.pdf")

    # 5) Multi-page: label, packing slip (no barcode), label
    doc = pymupdf.open()
    page = doc.new_page(width=_in(4), height=_in(6))
    _place(page, make_label_image(tracking="1Z999AA10123456784"), pymupdf.Rect(0, 0, _in(4), _in(6)))
    slip = doc.new_page(width=_in(8.5), height=_in(11))
    slip.insert_textbox(pymupdf.Rect(_in(1), _in(1), _in(7.5), _in(10)),
                        "PACKING SLIP\n\nOrder #10234\nQty  Item\n 1   Widget A\n 2   Gadget B\n",
                        fontsize=14, fontname="helv")
    page = doc.new_page(width=_in(4), height=_in(6))
    _place(page, make_label_image(carrier="USPS", tracking="9400111899223818110794"),
           pymupdf.Rect(0, 0, _in(4), _in(6)))
    save(doc, "multi.pdf")

    return written


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
    paths = generate(out)
    print(f"Generated {len(paths)} fixtures in {os.path.abspath(out)} (DPI={DPI}):")
    for p in paths:
        print(f"  {os.path.basename(p)}  {os.path.getsize(p)} bytes")
