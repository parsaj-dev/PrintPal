## PrintPal 0.10.0 — print size & position, one-click updates

### Fixed
- **One label no longer gets split in two.** With *Split 2-up / 4-up pages* on, a single label whose barcodes sit in separate sections — like a FedEx return label (reference barcode up top, tracking barcode below a rule) — was cut into two half-labels. A split is now only accepted when every piece is label-sized and the pieces together are bigger than one label could be. Real 2-up and 4-up sheets still split as before.
- **Big pages print in the right proportions.** A page larger than the printer bitmap safety cap (e.g. Letter on a 1200 dpi printer) was squashed on one axis; it now scales evenly.
- **Saved PNGs open at the real size** (the image carries its DPI), and history records the right DPI for labels opened from image files.
- **Upgrading restarts the virtual printer's watcher** right away (it used to stay stopped until the next sign-in), and a PrintPal that was running in the tray is put back there.

### New
- **Print size & position.** Instead of *Fit page*, print at a **percentage** of the label's actual size (100% = actual size) or fit it inside a **size in inches** (keep proportions, or stretch to exactly that size). Then place it anywhere on the page — 9 anchor positions plus a fine nudge in inches. A live preview draws your printer's actual page with the label on it and warns if any of it would fall off. Open it from the new **⤢ Fit** button in the action bar (**Ctrl+L**) or *Settings → Size & position*. Packing slips routed to the paper printer still print full page.
- **Installer: Update only.** Running the new installer over an existing install offers **Update only (recommended)** — one *Update now* click replaces the program and leaves settings, history, learned crops, shortcuts, the sign-in option and the PrintPal printer untouched. No questions. *Change install options* is still there if you want to go through the choices. Silent installs (`/SILENT`) over an existing install use update-only too.
- **Keep as one label.** If a page is ever split into several labels wrongly, a *Keep as one label* button next to "Label 1 of 2" re-reads it whole (just this time; the setting stays as it is).
