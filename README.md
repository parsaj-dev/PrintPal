# PrintPal

One-click shipping label cropper and printer for Windows. Click the taskbar icon, and the label from whatever PDF you just copied gets cropped, straightened, and sent to your DYMO LabelWriter 450.

## What it does

1. You download a return label PDF and copy the file (or its path) to the clipboard.
2. You click PrintPal in the taskbar.
3. It reads the file, finds the shipping label (anchoring on barcodes), crops out all the legal text and instructions, rotates it upright, and prints it.

If it's confident the crop is good, it just prints. If not, it shows a preview so you can check before printing.

## How the detection works

- Rasterizes the PDF page at 200 DPI with PyMuPDF.
- Finds barcodes with pyzbar. Their position and orientation anchor the crop.
- Grows from the barcode region to the full label using a two-pass gap merge (vertical then horizontal). The gap thresholds adapt to the actual whitespace distribution on each page rather than relying on hardcoded values.
- Adds a quiet-zone margin so barcodes aren't clipped.
- Rotates upright based on barcode orientation.
- Rechecks that barcodes still scan in the output. If they don't, confidence drops and it won't auto-print.

Falls back to the largest content block on the page if no barcodes are found, but flags this as low confidence.

## Input handling

In priority order:
1. A file path passed as a command line argument.
2. A file on the Windows clipboard (CF_HDROP, like right-click > Copy in Explorer).
3. A valid file path as clipboard text (handles Windows "Copy as path" with quotes).

Supports PDF, PNG, and JPEG. Never modifies or moves the source file.

## Setup

Requires Python 3.10+ on Windows.

```
pip install -e ".[dev]"
```

On Windows, pyzbar needs the zbar DLL. The PyInstaller build bundles it. For development, install zbar from https://github.com/mchehab/zbar or use the prebuilt Windows DLL.

## Configuration

Settings are stored in `%APPDATA%\PrintPal\config.toml`. Created with defaults on first run:

- `printer` -- name of the printer (default: "DYMO LabelWriter 450")
- `media_size` -- label size (default: "4x6")
- `dpi` -- rasterization DPI (default: 200)
- `crop_margin_inches` -- extra margin around the detected label (default: 0.06)

Change settings from the preview window's Settings button, or edit the file directly.

## Building the executable

```
pip install -e ".[dev]"
pyinstaller printpal.spec
```

The output lands in `dist/PrintPal.exe`. Pin it to the taskbar.

The spec bundles the zbar DLL automatically. If you're on a fresh machine, make sure the DLL is in your PATH or the pyzbar package directory before building.

## Running tests

```
pytest tests/ -v
```

The test suite uses a real FedEx return label fixture and verifies that the crop finds the label, excludes the instruction text, and the output barcodes still decode.

## Project structure

```
src/printpal/
    __init__.py      -- package version
    main.py          -- entry point, CLI arg + clipboard + single-instance guard
    detect.py        -- barcode detection, gap-merge cropping, rotation, recheck
    rasterize.py     -- PDF/image to PIL Image via PyMuPDF
    clipboard.py     -- Windows clipboard: CF_HDROP and text path
    printing.py      -- Windows GDI print spooler
    config.py        -- TOML config in AppData
    log.py           -- rotating file logger
    ui.py            -- tkinter preview window and settings dialog
tests/
    fixtures/        -- sample PDFs
    test_detect.py   -- detection integration and unit tests
    test_config.py   -- config round-trip tests
```
