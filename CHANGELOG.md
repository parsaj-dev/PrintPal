# Changelog

## 0.7.0

A big release: a virtual printer, batch printing, history + reprint, N-up
splitting, smart routing, and a completely new modern UI -- plus detection and
print-quality fixes.

### Added

- **Virtual "PrintPal" printer (Windows).** Print to PrintPal from any app
  (File > Print > PrintPal) and the pages flow straight into the crop-and-print
  engine -- no downloading, no file hunting. Jobs are captured as **XPS** and
  read natively by MuPDF, so there is **no Ghostscript and no AGPL** in the app.
  Optional, cleanly separated (`winprinter/`), with install/uninstall scripts and
  a Windows test plan. Installable from the setup wizard.
- **Modern UI (PySide6/Qt)** with light/dark themes and the orange/brown brand,
  replacing the old tkinter/ttk window. Native and fast on slow hardware; no
  WebView2/browser runtime.
- **Batch queue.** Drop a folder, several files, or one PDF full of labels and
  print them all with one click, with per-label status (queued/printing/done/
  failed) and retry. Built to pair with a future Downloads-watcher.
- **History + reprint.** Every print is logged (thumbnail, date, carrier,
  tracking number) to a local SQLite database with the exact print image.
  One-click reprint from history, and **Ctrl+R** to reprint the last label.
- **N-up splitting.** Pages holding 2 or 4 labels (Amazon/Etsy style) are
  detected and split into individual 4x6 prints (`split_nup`, on by default).
- **Smart routing (opt-in).** When enabled for a job, 4x6 labels go to the
  thermal printer and packing slips / A4 to the paper printer -- per page, never
  automatic (`thermal_printer` / `paper_printer`).
- **XPS/OXPS input** support (in addition to PDF and images).
- Carrier + tracking-number recognition from the shipping barcode.

### Fixed

- Image-file inputs reported the wrong physical size (the detection DPI was
  assumed instead of the image's real resolution), which also picked the wrong
  media strategy. Now reads the image's DPI metadata with a sensible fallback.
- Packing slips / instruction sheets were counted as shipping labels. Pages are
  now classified as label / document / blank.
- On Letter/A4 sheets the top of the label (carrier band + address) could be
  cropped off when the closing left it as a separate block; stacked label blocks
  are now reattached across small gaps without swallowing instructions.
- Multiple copies emitted separate spooler documents; now a single job with N
  pages.

### Notes

- The virtual-printer install method defaults to an in-box XPS driver + a file
  port + a logon watcher (no third-party software). A RedMon-based redirection
  method is documented as a more-robust alternative. See `winprinter/README.md`
  for the design, licensing and the Windows acceptance test plan.

## 0.6.0 and earlier

See the git history and PR #1 for the modular rebuild (detection, multi-page,
worker threads, print quality) that preceded this release.
