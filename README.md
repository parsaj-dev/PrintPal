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
- [Features](#features)
- [Download](#download)
- [Print to PrintPal from any app](#print-to-printpal-from-any-app)
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

## Features

- **Print to PrintPal from any app** -- an optional virtual printer (File > Print > PrintPal) sends any app's pages straight into the crop-and-print engine. No downloading, no file hunting. License-clean (XPS -> MuPDF, no Ghostscript). See [below](#print-to-printpal-from-any-app).
- **Modern UI** -- a clean PySide6 interface with light/dark themes and the orange/brown brand. Native and fast even on a slow Windows 10 PC (no browser runtime).
- **Batch queue** -- drop a folder, several files, or one PDF full of labels and print them all with one click, with a per-label status list (queued / printing / done / failed) and one-click retry.
- **History + reprint** -- every print is logged (thumbnail, date, carrier, tracking number decoded from the barcode) in a local database. One-click reprint from history, or **Ctrl+R** to reprint the last label.
- **N-up splitting** -- a page holding 2 or 4 labels (Amazon/Etsy style) is detected and split into individual 4x6 prints automatically.
- **Smart routing (opt-in)** -- when you enable it for a job, 4x6 labels go to the thermal printer and packing slips / A4 sheets to the paper printer, per page. Off by default; never automatic.
- **Auto-print** -- optionally print a single high-confidence label the moment it loads.

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

If no barcode is found, PrintPal keeps the whole page (or the largest content block) and flags it as low confidence rather than guessing at a tight crop. A document-media page with no barcode is classified as a **packing slip / document** rather than a shipping label.

**N-up pages** (2 or 4 labels in a grid, Amazon/Etsy style) are detected by clustering the barcodes and cutting the page at the whitespace gutters *between* the labels, so each one is cropped and printed as an individual 4x6. This can be turned off (`split_nup`) to keep the whole page as one.

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
| `thermal_printer` | `` | Printer for 4x6 labels when smart routing is enabled (falls back to `printer`) |
| `paper_printer` | `` | Printer for packing slips / A4 when smart routing is enabled |
| `media_size` | `4x6` | Label media size |
| `detect_dpi` | `200` | Detection render DPI (lower = faster, but barcodes may not decode below ~180) |
| `print_dpi` | `300` | Print render DPI |
| `crop_margin_inches` | `0.08` | Quiet-zone margin around the detected label |
| `copies` | `1` | Copies per print |
| `auto_print` | `false` | Print a single high-confidence label automatically |
| `auto_print_min_confidence` | `0.85` | Confidence needed for auto-print |
| `split_nup` | `true` | Split multi-label (N-up) pages into individual labels |
| `dark_mode` | `false` | Use the dark theme |

Smart routing is only *applied* when you tick it for a job in the Queue -- setting `thermal_printer` / `paper_printer` never reroutes prints on its own.

Older config files that used `dpi` are still read (it maps to `detect_dpi`).

## Building from source

Requires Python 3.10+ on Windows. On Windows, pyzbar needs the zbar DLL -- the PyInstaller build bundles it automatically. The UI is built on **PySide6/Qt** (LGPL); its DLLs are bundled by PyInstaller and unused Qt modules are trimmed by `printpal.spec`.

```bash
pip install -e ".[dev]"
pyinstaller printpal.spec
```

The output is `dist/PrintPal/` (containing `PrintPal.exe` and the virtual-printer helper `PrintPalPort.exe`).

Releases are built automatically by GitHub Actions on a Windows runner when a version tag is pushed.

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Unit tests run without any fixtures -- the barcode-dependent ones inject fake barcodes, so the geometry, orientation, printing and config logic are all covered headless (no Windows, no display). To also run the end-to-end integration tests, drop a real shipping label PDF at `tests/fixtures/sample_label.pdf`.

## Project structure

The core engine (`detect`, `rasterize`, `pipeline`, `printing`-prep, `ingest`,
`batch`, `history`, `routing`, `carrier`) is UI-free and cross-platform, so it is
fully unit-tested headless. The desktop UI (`qtui`) and the Windows virtual
printer (`winprinter`) sit on top of it.

```
src/printpal/
    main.py          entry point: arg/clipboard input, single-instance, spool drain
    detect.py        label detection + N-up splitting, cropping, rotation, recheck
    pipeline.py      turns a file into ready-to-print labels (multi-page, N-up, lazy render)
    rasterize.py     PDF/XPS/image to PIL Image via PyMuPDF (page + region rendering)
    printing.py      Windows GDI print spooler, Lanczos-to-device scaling
    ingest.py        job spool: one hand-off point for the virtual printer,
                     a second launch, a future Downloads-watcher, batch queue
    batch.py         per-label print queue (status, retry, history logging)
    history.py       SQLite print history + thumbnails + reprint (AppData)
    carrier.py       carrier + tracking-number recognition from barcodes
    routing.py       opt-in smart routing (labels -> thermal, documents -> paper)
    clipboard.py     Windows clipboard (CF_HDROP, text path, file:/// URL)
    config.py        tolerant TOML config in AppData
    log.py           rotating file logger
    qtui/            the PySide6 desktop UI
        app.py, window.py, widgets.py, workers.py, settings.py, theme.py
winprinter/          optional Windows virtual printer (XPS -> ingest -> engine)
    jobio.py, printpal_catcher.py, printpal_watcher.py, printpal_port.py
    install_printer.ps1, uninstall_printer.ps1, README.md
tools/               dev utilities (not shipped in the app)
    generate_fixtures.py   synthetic labels with real barcodes for tests
    xps.py                 pack PIL pages into an XPS (simulates the driver)
tests/                 detect, pipeline, ingest, xps, winprinter, batch,
                       history, carrier, routing, config, rasterize, printing
```
