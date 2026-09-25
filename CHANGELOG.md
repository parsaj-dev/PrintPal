# Changelog

## 0.8.0

### Faster: print-to-PrintPal and printing
- **The virtual printer hands jobs over ~2-3 s sooner.** The watcher now spots
  a finished job by its end marker (an XPS's ZIP end record, a PDF's `%%EOF`)
  on a 0.25 s poll, instead of waiting for the file size to hold still across
  two 1-second polls.
- **No more process launch per job when PrintPal is open.** The watcher (and
  the RedMon catcher) move the job straight into the spool the running app
  watches, instead of starting `PrintPal.exe --ingest` just to pass it along --
  seconds saved per label on an old PC.
- **The app sees new jobs instantly** (a file-system notification rather than
  a 0.7 s poll), and drains queued jobs back to back.
- **Keeps running in the tray** when closed, and can start there when you sign
  in (installer option, and in Settings), so there is no cold start between
  "Print" and the preview. A second launch brings the existing window forward.
- **Printing never freezes the window.** Rendering, spooling and the history
  write all run in the background (prints go out in order); the print image is
  pre-rendered while you look at the preview, so Print is immediate; auto-print
  starts the moment detection finishes.
- History PNGs are written with fast compression; the history list is only
  built when you open it; queue rows update individually instead of the whole
  list being rebuilt per label; the preview is rescaled once per size rather
  than on every repaint.
- Startup no longer waits for `schtasks` or a printer check.
- Packing slips on Letter/A4 skip the slow all-barcode-types rescan (see below).

### Added
- **Close** a label without printing (button, Esc or Ctrl+W), **Cancel** a
  running detection, **Stop** a batch, and remove single items from the queue.
- Opening a file while another is still being read now replaces it instead of
  being silently ignored.
- Ctrl+V pastes a label (the README promised it; now it's wired up).

### Smart routing
- One saved on/off setting, shared by Settings and toggles in the Label and
  Queue views (previously a per-session checkbox in the Queue only, and the
  Label view ignored routing entirely).
- **Documents routed to the paper printer print as the full page**, not the
  cropped content block.
- Each label shows where it will print ("→ HP LaserJet (full page)"); the
  toggle is only offered once two different printers are chosen.
- A retail EAN/UPC barcode on a Letter/A4 packing slip no longer makes it count
  as a shipping label (which would have routed it to the label printer).

### Fixed
- **Settings (and other dialogs) were unreadable** -- dark background with dark
  text -- on PCs using the Windows dark colour scheme. The app now themes every
  window and dialog itself, independent of the Windows setting.
- Spin boxes and drop-downs had no visible arrows; check boxes had no tick.
- Settings no longer freezes while it lists printers (it reuses the list the
  window already loaded in the background).
- The unused "Media size" setting is gone from Settings (the page size always
  comes from the printer driver).
- Spooled print jobs are now deleted once they're closed or replaced, instead
  of piling up until the next start.
- Upgrading/uninstalling while PrintPal or the printer watcher is running no
  longer fails with "file in use".
- XPS/OXPS files copied in Explorer are accepted from the clipboard.

## 0.7.1

### Fixed
- **Virtual printer stopped when its console window was closed.** The logon
  watcher now runs as a window-less `PrintPalWatcher.exe`, restarts itself if it
  dies, has no time limit (the Windows default would have stopped it after 3
  days), and opening PrintPal restarts it if needed.

### Faster (same output)
Detection results are byte-for-byte identical to 0.7.0 on the test labels;
it just does less work:
- Barcodes are scanned once per page instead of up to three times, and N-up
  cells reuse the page scan.
- zbar tries the shipping symbologies first (much faster), falling back to a
  full scan only when nothing is found, so no barcode type is missed.
- Blank pages skip the barcode scan; redundant post-crop re-scans are replaced
  by an exact geometric check.
- Greyscale is computed once per page; multi-page PDFs are opened once.
- The window opens before the detection engine loads, and the engine warms up
  in the background.
- No more printer enumeration at startup or before every print (can take
  seconds on office PCs with network printers); the printer list loads in the
  background.
- Print data is packed for the spooler in C instead of a Python loop.

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
