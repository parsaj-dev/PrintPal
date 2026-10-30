# PrintPal virtual printer (Windows)

Install a **PrintPal** printer so that from *any* app — Chrome, Adobe Reader,
your carrier's web portal — you can **File → Print → PrintPal** and the rendered
pages flow straight into PrintPal's crop-and-print engine. No saving the PDF, no
hunting through Downloads.

This component is **optional and cleanly separated** from the core app. The core
engine never depends on it; it only depends on the core app's `--ingest` CLI.

---

## How it works

```
   Any app  ──File>Print──▶  "PrintPal" printer ──▶ (capture) ──▶ PrintPalPort
   (Chrome…)                 (in-box XPS driver)                       │
                                     PrintPal running? ──yes──▶ move job into its spool
                                                       └─no───▶ PrintPal.exe --ingest <job.xps>
                                                                       │
                                            spool ─▶ SAME detect+crop+print engine
```

The watcher takes a job the moment it is complete -- it polls every 0.25 s and
recognises the end of the file (an XPS's ZIP end-of-central-directory record, or
a PDF's `%%EOF`) rather than waiting for the size to stop changing. When PrintPal
is already running (e.g. in the tray) the job goes straight into its spool with
no process launch; the app is notified by the file system and shows the label
within a fraction of a second.

The job is captured as **XPS** (the OpenXPS page format Windows print drivers
already produce) and handed to PrintPal, which reads it with **PyMuPDF/MuPDF —
natively, no Ghostscript**. It is then detected, cropped and printed exactly like
a dropped PDF (`printpal.ingest` → `printpal.pipeline.process_file`).

### Two install methods

| | FilePort (default) | Redirected |
|---|---|---|
| Extra software | **None** (in-box driver only) | A redirection port monitor (e.g. RedMon) |
| Catches prints when PrintPal is closed | Yes — a logon watcher grabs them | Yes — the monitor launches PrintPal |
| Mechanism | XPS driver → file port → `PrintPalPort watch` → `--ingest` | port → `PrintPalPort catch` (stdin) → `--ingest` |
| Robustness | Good for one-at-a-time label printing | Best for high volume / concurrent jobs |
| License | Fully in-box, no GPL/AGPL | RedMon is **GPL** (installed by you, never bundled) |

Both feed the **same** engine and neither needs Ghostscript.

---

## Licensing (important for this public repo)

- The PrintPal app and this component are the project's own code (see the repo
  `LICENSE`). The captured format is **XPS**, read by our existing MuPDF
  dependency, so **no Ghostscript is required and no AGPL code is involved**.
- **RedMon** (only if you choose the Redirected method) is **GPL**. It is *not*
  bundled or redistributed here — you install it yourself. Its licence is its
  own; keeping it a user-installed, optional dependency keeps this repo clean.
- A **PostScript** driver + **Ghostscript** path also exists in the catcher
  (`--ghostscript`), but Ghostscript is **AGPL**. It is **off by default** and
  never bundled. Only enable it if you understand the AGPL implications for how
  you redistribute.

---

## Install

From the folder containing `PrintPalPort.exe` (shipped next to `PrintPal.exe`):

```powershell
# Default, no extra software:
powershell -ExecutionPolicy Bypass -File .\install_printer.ps1

# Or the redirection-monitor method (install RedMon first):
powershell -ExecutionPolicy Bypass -File .\install_printer.ps1 -Method Redirected
```

The script self-elevates (adding a printer/port needs admin). Uninstall:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_printer.ps1
```

Paths used:

- Spool incoming: `%APPDATA%\PrintPal\spool\incoming\`
- Catcher/watcher log: `…\incoming\catcher.log`, `…\incoming\watcher.log`
- Logon watcher task (FilePort): `PrintPalPortWatcher` — runs `PrintPalWatcher.exe` hidden (no window), restarts if it dies, no time limit. Opening PrintPal also restarts it.

---

## Pieces

| File | Role |
|---|---|
| `jobio.py` | Format sniffing (PDF/XPS/PS) + staging. Pure, unit-tested. |
| `printpal_catcher.py` | `catch`: read a job (stdin/file) → stage → `--ingest`. |
| `printpal_watcher.py` | `watch`: watch the incoming folder → hand off finished jobs. |
| `printpal_port.py` | One entry point → `PrintPalPort.exe {catch\|watch}` (console) and `PrintPalWatcher.exe` (same code, no window — what the logon task runs). |
| `install_printer.ps1` / `uninstall_printer.ps1` | Install / remove the printer. |

The OS-independent logic (sniffing, staging, catcher routing) is covered by
`tests/test_winprinter.py`. The Windows print-stack wiring can only be validated
on Windows — use the checklist below.

---

## Windows test plan

> These steps cannot be run in the Linux CI/sandbox; they are the manual
> acceptance test for the printer plumbing.

**Setup**
1. Build `PrintPal.exe` and `PrintPalPort.exe` (`pyinstaller printpal.spec`).
2. Run `install_printer.ps1`. Approve the UAC prompt.
3. Confirm **PrintPal** appears in *Settings → Printers & scanners*.

**Core flow (FilePort)**
4. In Chrome, open any shipping-label PDF/page → **Print** → choose **PrintPal**
   → Print.
5. Expect: PrintPal opens (or comes to front) showing the detected, cropped
   label with a confidence badge, within a couple of seconds.
6. Check `%APPDATA%\PrintPal\spool\incoming\watcher.log` shows the hand-off and
   no errors. `incoming\` should not accumulate `out.xps` (it gets claimed).

**Format coverage**
7. Repeat from: Adobe Reader; Edge; a Word doc; a multi-label PDF; a Letter
   sheet with a label in a corner; a rotated label. Each should crop correctly.

**App-closed behaviour**
8. Close PrintPal. Print again. The logon watcher should catch the job and
   launch PrintPal. (If you skipped the watcher task, the job waits in
   `incoming\` until PrintPal next runs.)

**Concurrency**
9. Print two jobs in quick succession. Both should arrive as separate labels
   (the watcher renames each stable file before the next print overwrites it).

**Prompting driver check**
10. If a *Save As* dialog appears instead of silent capture, the in-box XPS
    driver on your Windows build prompts. Re-install with `-Method Redirected`
    (after installing RedMon) — that captures the stream regardless of driver.

**Uninstall**
11. Run `uninstall_printer.ps1`. Confirm the printer, its port and the
    `PrintPalPortWatcher` task are gone.

**Print quality** (covers the sandbox-untestable GDI path too)
12. Send a caught label to a real 4×6 thermal printer (DYMO/Rollo/Zebra).
    Confirm the barcode scans and text is crisp (Lanczos-to-device scaling).
