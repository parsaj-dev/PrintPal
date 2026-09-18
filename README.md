<p align="center">
  <img src="assets/icon.png" width="96" alt="PrintPal icon">
</p>

<h1 align="center">PrintPal</h1>

<p align="center">
  One-click shipping label cropper and printer for Windows.
</p>

<p align="center">
  <a href="https://github.com/parsaj-dev/PrintPal/releases/latest"><img src="https://img.shields.io/github/v/release/parsaj-dev/PrintPal?style=flat-square&color=orange" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2B-blue?style=flat-square" alt="Windows 10+">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square" alt="Python 3.10+">
  <a href="https://github.com/parsaj-dev/PrintPal/blob/main/LICENSE"><img src="https://img.shields.io/github/license/parsaj-dev/PrintPal?style=flat-square" alt="License"></a>
</p>

---

## Table of Contents

- [What it does](#what-it-does)
- [Download](#download)
- [How the detection works](#how-the-detection-works)
- [Input handling](#input-handling)
- [Configuration](#configuration)
- [Building from source](#building-from-source)
- [Running tests](#running-tests)
- [Project structure](#project-structure)

## What it does

1. Copy a shipping-label PDF (or click **Open**, or press **Ctrl+V**).
2. PrintPal reads the file, finds the label, crops off the legal text and instructions, straightens it, and shows it in a live preview.
3. Check the crop, pick your printer, and hit **Print**.

The window shows the detected label at print quality with a confidence read-out. Multi-page files (several labels, or a label plus a packing slip) get a thumbnail rail so you can page through and print any of them. You can rotate a label by hand, set the number of copies, or save the crop as a PNG.

Prefer no clicks? Turn on **auto-print** in Settings and a single, high-confidence label prints the moment it loads.

Designed for thermal label printers like the DYMO LabelWriter 450, Rollo, and Zebra. Works with FedEx, UPS, Purolator, Amazon, and any label PDF or image with barcodes -- whether it arrives as a bare 4x6 or buried on a Letter/A4 sheet.

## Download

Go to the [Releases page](https://github.com/parsaj-dev/PrintPal/releases/latest) and grab:

- **`PrintPal-Setup.exe`** -- installer with Start Menu shortcut, desktop shortcut, and Add/Remove Programs entry. Recommended.
- **`PrintPal-Portable.zip`** -- extract anywhere and run. No install needed.

No Python or other dependencies required. Everything is bundled.

## How the detection works

No cloud services, no AI, no network access. Everything runs locally.

PrintPal reads the page's **physical size** first and picks one of two strategies:

- **Bare label media** (a page whose short side is roughly 6 inches or less -- a 4x6, 4x8, etc.): the page *is* the label. PrintPal trims the outer white margin so the label fills the media and prints it.
- **Document media** (Letter, A4): the label is a block somewhere on the sheet. PrintPal closes the page's ink into solid regions and keeps the connected region that carries the barcodes, then unions in any barcode set apart by a divider. The closing bridges *intra-label* gaps (address line spacing, the gap between an address block and its barcode) without reaching across the wider whitespace that separates the label from instructions -- which is what keeps the whole address attached to the barcode instead of getting sliced off.

Then, for both strategies:

- Barcodes are found with [pyzbar](https://github.com/NaturalHistoryMuseum/pyzbar); their orientation is used to rotate the crop upright (a rotated FedEx or Amazon return label comes out straight).
- A quiet-zone margin is added so barcodes are never clipped.
- The crop is re-scanned to confirm the barcodes still decode. If they don't, the confidence drops so you know to check the preview.

Detection runs on a low-DPI render for speed; the label you print is then re-rendered from the PDF at full print resolution -- only the label's region, so a big sheet never gets rasterized at high DPI just to keep a 4x6 corner.

If no barcode is found, PrintPal keeps the whole page (or the largest content block) and flags it as low confidence rather than guessing at a tight crop.

## Input handling

PrintPal accepts a label from any of:

1. **Print to PrintPal** -- from any app's **File > Print**, pick the **PrintPal** printer (see below).
2. **Command line** -- `PrintPal.exe "C:\Downloads\label.pdf"`.
3. **Clipboard file** -- right-click a file in Explorer and Copy, then press **Ctrl+V** (or launch PrintPal).
4. **Clipboard path or URL** -- "Copy as path" (Ctrl+Shift+C) or a `file:///` URL from a browser.
5. **Open button** -- pick a file from the window.

Supports PDF and XPS/OXPS documents plus PNG, JPEG, TIFF, BMP, GIF and WebP images. The source file is never modified, moved, or deleted. A second launch while PrintPal is open hands its file to the existing window instead of opening a duplicate.

## Print to PrintPal from any app

Install the optional **PrintPal virtual printer** and you can skip downloading and file-hunting entirely: in Chrome, Adobe Reader, or your carrier's portal, choose **File > Print > PrintPal**, and the rendered pages flow straight into PrintPal's crop-and-print engine.

It captures each job as **XPS**, which PrintPal reads with its existing PDF engine (PyMuPDF/MuPDF) -- so there is **no Ghostscript and no AGPL code** involved, and it feeds the exact same detection and printing path as a dropped file. Install it during setup (tick "Install the PrintPal virtual printer") or later:

```powershell
powershell -ExecutionPolicy Bypass -File "install_printer.ps1"
```

Full design, licensing notes, install/uninstall, and the Windows test plan are in [`winprinter/README.md`](winprinter/README.md).

## Configuration

Settings live in `%APPDATA%\PrintPal\config.toml`, created with defaults on first run. Change them from the **Settings** button, or edit the file by hand:

| Setting | Default | What it does |
|---|---|---|
| `printer` | `DYMO LabelWriter 450` | Name of the target printer |
| `media_size` | `4x6` | Label media size |
| `detect_dpi` | `200` | Detection render DPI (lower = faster, but barcodes may not decode below ~180) |
| `print_dpi` | `300` | Print render DPI |
| `crop_margin_inches` | `0.08` | Quiet-zone margin around the detected label |
| `copies` | `1` | Copies per print |
| `auto_print` | `false` | Print a single high-confidence label automatically |
| `auto_print_min_confidence` | `0.85` | Confidence needed for auto-print |

Older config files that used `dpi` are still read (it maps to `detect_dpi`).

## Building from source

Requires Python 3.10+ on Windows. On Windows, pyzbar needs the zbar DLL -- the PyInstaller build bundles it automatically.

```bash
pip install -e ".[dev]"
pyinstaller printpal.spec
```

The output is `dist/PrintPal.exe`.

Releases are built automatically by GitHub Actions on a Windows runner when a version tag is pushed.

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Unit tests run without any fixtures -- the barcode-dependent ones inject fake barcodes, so the geometry, orientation, printing and config logic are all covered headless (no Windows, no display). To also run the end-to-end integration tests, drop a real shipping label PDF at `tests/fixtures/sample_label.pdf`.

## Project structure

```
src/printpal/
    main.py          entry point, input resolution, single-instance handoff, auto-print
    detect.py        physical-size-aware label detection, cropping, rotation, recheck
    pipeline.py      turns a file into ready-to-print labels (multi-page, lazy hi-res render)
    rasterize.py     PDF/image to PIL Image via PyMuPDF (page + region rendering)
    clipboard.py     Windows clipboard (CF_HDROP, text path, file:/// URL)
    printing.py      Windows GDI print spooler, Lanczos-to-device scaling
    ingest.py        job spool: one hand-off point for the virtual printer,
                     a second launch, a future Downloads-watcher, batch queue
    config.py        tolerant TOML config in AppData
    log.py           rotating file logger
    theme.py         ttk design system (palette, fonts, styles)
    ui.py            main window, preview, thumbnail rail, settings
winprinter/          optional Windows virtual printer (XPS -> ingest -> engine)
    jobio.py, printpal_catcher.py, printpal_watcher.py, printpal_port.py
    install_printer.ps1, uninstall_printer.ps1, README.md
tools/               dev utilities (not shipped in the app)
    generate_fixtures.py   synthetic labels with real barcodes for tests
    xps.py                 pack PIL pages into an XPS (simulates the driver)
tests/
    test_detect.py     detection unit + integration tests
    test_pipeline.py   orchestration, manual rotation, print render
    test_printing.py   image prep + DIB packing
    test_ingest.py     job spool submit/claim/complete
    test_xps.py        XPS ingest end-to-end (virtual-printer path)
    test_winprinter.py catcher/watcher format sniffing + staging
    test_config.py     config round-trip + tolerance
    test_rasterize.py  loader helpers + image DPI
```
