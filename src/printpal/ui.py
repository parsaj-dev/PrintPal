"""PrintPal's desktop UI.

One resizable main window that walks a person from "I have a label file" to a
printed label: a live preview of the detected crop, a confidence read-out, a
thumbnail rail for multi-page files, manual rotate/reset, printer + copies
controls, and a big Print button. Detection runs on a worker thread so the
window never freezes on a slow machine.

Only the stdlib `tkinter` plus Pillow are used, so the frozen build stays small
and starts fast. Windows-only bits (printing) are imported lazily.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from printpal import theme
from printpal.config import Config
from printpal.pipeline import ProcessedLabel, process_file
from printpal.printing import list_printers


def _asset_path(name: str) -> str | None:
    """Locate a bundled asset in dev and in a PyInstaller build."""
    roots = []
    if hasattr(sys, "_MEIPASS"):
        roots.append(os.path.join(sys._MEIPASS, "assets"))
        roots.append(sys._MEIPASS)
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(os.path.join(here, "..", "..", "assets"))
    roots.append(os.path.join(here, "assets"))
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _set_window_icon(win: tk.Tk) -> ImageTk.PhotoImage | None:
    path = _asset_path("icon.png")
    if not path:
        return None
    try:
        img = Image.open(path).convert("RGBA")
        photo = ImageTk.PhotoImage(img)
        win.iconphoto(True, photo)
        return photo
    except Exception:
        return None


class _Tooltip:
    """A tiny hover tooltip. No dependencies, disappears on leave/click."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(550, self._show)

    def _show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() - 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.configure(background=theme.INK)
        lbl = tk.Label(self.tip, text=self.text, background=theme.INK,
                       foreground="#FFFFFF", padx=8, pady=3,
                       font=(theme.pick_family(self.widget), 8))
        lbl.pack()
        self.tip.update_idletasks()
        self.tip.wm_geometry(f"+{x - self.tip.winfo_width() // 2}"
                             f"+{y - self.tip.winfo_height()}")

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None


class MainWindow:
    """The primary PrintPal window."""

    MIN_W, MIN_H = 860, 600

    def __init__(self, config: Config, initial_path: str | None = None,
                 on_ready: callable | None = None):
        self.config = config
        self.labels: list[ProcessedLabel] = []
        self.selected = 0
        self._preview_photo = None
        self._thumb_photos: list[ImageTk.PhotoImage] = []
        self._logo_photo = None
        self._icon_photo = None
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._on_ready = on_ready
        self._last_preview_size = (0, 0)

        self.root = tk.Tk()
        self.root.title("PrintPal")
        self.root.minsize(self.MIN_W, self.MIN_H)
        self.root.geometry("980x680")
        self._icon_photo = _set_window_icon(self.root)

        self.style, self.fonts = theme.apply_theme(self.root)

        self._build_header()
        self._build_body()
        self._build_actionbar()
        self._build_statusbar()

        self.root.bind("<Control-v>", lambda e: self.paste_from_clipboard())
        self.root.bind("<Control-o>", lambda e: self.open_dialog())
        self.root.bind("<Control-p>", lambda e: self._print_current())
        self.root.bind("<Left>", lambda e: self._select_delta(-1))
        self.root.bind("<Right>", lambda e: self._select_delta(1))
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self._center()
        if initial_path:
            self.root.after(80, lambda: self.load_path(initial_path))
        else:
            self._show_empty()
        self.root.after(100, self._poll_queue)

    # -- layout scaffolding ----------------------------------------------------
    def _build_header(self):
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(18, 12))
        header.pack(side="top", fill="x")

        left = ttk.Frame(header, style="Header.TFrame")
        left.pack(side="left")

        logo_path = _asset_path("icon.png")
        if logo_path:
            try:
                img = Image.open(logo_path).convert("RGBA").resize((34, 34), Image.LANCZOS)
                self._logo_photo = ImageTk.PhotoImage(img)
                tk.Label(left, image=self._logo_photo, background=theme.ORANGE,
                         borderwidth=0).pack(side="left", padx=(0, 10))
            except Exception:
                pass

        titles = ttk.Frame(left, style="Header.TFrame")
        titles.pack(side="left")
        ttk.Label(titles, text="PrintPal", style="Wordmark.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Shipping label cropper & printer",
                  style="HeaderSub.TLabel").pack(anchor="w")

        right = ttk.Frame(header, style="Header.TFrame")
        right.pack(side="right")
        ttk.Button(right, text="Paste  (Ctrl+V)", style="HeaderGhost.TButton",
                   command=self.paste_from_clipboard).pack(side="right", padx=(8, 0))
        ttk.Button(right, text="Open file…", style="HeaderGhost.TButton",
                   command=self.open_dialog).pack(side="right")

        ttk.Frame(self.root, style="Sep.TFrame", height=1).pack(side="top", fill="x")

    def _build_body(self):
        self.body = ttk.Frame(self.root, style="App.TFrame")
        self.body.pack(side="top", fill="both", expand=True)

    def _build_actionbar(self):
        ttk.Frame(self.root, style="Sep.TFrame", height=1).pack(side="top", fill="x")
        bar = ttk.Frame(self.root, style="App.TFrame", padding=(16, 10))
        bar.pack(side="top", fill="x")

        # left: rotate + save + settings tools (compact so the printer combo has room)
        left = ttk.Frame(bar, style="App.TFrame")
        left.pack(side="left")
        self.btn_rot_ccw = ttk.Button(left, text="↺", width=3, style="Tool.TButton",
                                      command=lambda: self._rotate(False))
        self.btn_rot_ccw.pack(side="left")
        self.btn_rot_cw = ttk.Button(left, text="↻", width=3, style="Tool.TButton",
                                     command=lambda: self._rotate(True))
        self.btn_rot_cw.pack(side="left", padx=(6, 0))
        self.btn_save = ttk.Button(left, text="Save…", style="Tool.TButton",
                                   command=self._save_current)
        self.btn_save.pack(side="left", padx=(10, 0))
        self.btn_settings = ttk.Button(left, text="⚙", width=3, style="Tool.TButton",
                                       command=self.open_settings)
        self.btn_settings.pack(side="left", padx=(6, 0))
        _Tooltip(self.btn_rot_ccw, "Rotate left")
        _Tooltip(self.btn_rot_cw, "Rotate right")
        _Tooltip(self.btn_save, "Save the cropped label as a PNG")
        _Tooltip(self.btn_settings, "Settings")

        # right: print is pinned to the edge so it can never be clipped; the
        # printer + copies controls sit just to its left.
        self.btn_print = ttk.Button(bar, text="Print  ▸", style="Primary.TButton",
                                    command=self._print_current)
        self.btn_print.pack(side="right", padx=(14, 0))

        controls = ttk.Frame(bar, style="App.TFrame")
        controls.pack(side="right")
        ttk.Label(controls, text="Copies", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self.copies_var = tk.StringVar(value=str(self.config.copies))
        self.copies_spin = ttk.Spinbox(controls, from_=1, to=99, width=3,
                                       textvariable=self.copies_var)
        self.copies_spin.pack(side="left", padx=(0, 12))

        ttk.Label(controls, text="Printer", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self.printer_var = tk.StringVar(value=self.config.printer)
        self.printer_combo = ttk.Combobox(controls, textvariable=self.printer_var,
                                          width=22, state="readonly")
        self.printer_combo.pack(side="left")
        self.printer_combo.bind("<<ComboboxSelected>>", self._on_printer_change)
        self._refresh_printers()

    def _build_statusbar(self):
        ttk.Frame(self.root, style="Sep.TFrame", height=1).pack(side="top", fill="x")
        bar = ttk.Frame(self.root, style="Status.TFrame", padding=(14, 5))
        bar.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        self.status_progress = ttk.Progressbar(bar, style="Brand.Horizontal.TProgressbar",
                                                mode="indeterminate", length=120)

    # -- body states -----------------------------------------------------------
    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _show_empty(self):
        self._clear_body()
        self._set_actions_enabled(False)
        wrap = ttk.Frame(self.body, style="App.TFrame")
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        card = ttk.Frame(wrap, style="Card.TFrame", padding=(46, 40))
        card.pack()

        if self._logo_big():
            tk.Label(card, image=self._logo_big(), background=theme.SURFACE,
                     borderwidth=0).pack(pady=(0, 14))
        ttk.Label(card, text="Drop in a shipping label", style="CardH2.TLabel").pack()
        ttk.Label(card, text="PrintPal finds the label, crops off the clutter,\n"
                             "straightens it, and sends it to your label printer.",
                  style="CardMuted.TLabel", justify="center").pack(pady=(8, 20))

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.pack()
        ttk.Button(btns, text="Open a file…", style="Primary.TButton",
                   command=self.open_dialog).pack(side="left")
        ttk.Button(btns, text="Paste from clipboard", style="Ghost.TButton",
                   command=self.paste_from_clipboard).pack(side="left", padx=(10, 0))

        ttk.Label(card, text="Tip: copy a label PDF (or press Ctrl+V) and it loads instantly.",
                  style="CardMuted.TLabel").pack(pady=(20, 0))
        self.status_var.set("Waiting for a label…")

    def _logo_big(self):
        if getattr(self, "_logo_big_photo", None) is not None:
            return self._logo_big_photo
        path = _asset_path("icon.png")
        if not path:
            self._logo_big_photo = None
            return None
        try:
            img = Image.open(path).convert("RGBA").resize((72, 72), Image.LANCZOS)
            self._logo_big_photo = ImageTk.PhotoImage(img)
        except Exception:
            self._logo_big_photo = None
        return self._logo_big_photo

    def _show_loading(self, message: str):
        self._clear_body()
        self._set_actions_enabled(False)
        wrap = ttk.Frame(self.body, style="App.TFrame")
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(wrap, text=message, style="H1.TLabel").pack()
        pb = ttk.Progressbar(wrap, style="Brand.Horizontal.TProgressbar",
                             mode="indeterminate", length=240)
        pb.pack(pady=(16, 0))
        pb.start(12)
        self._loading_pb = pb

    def _show_error(self, title: str, message: str):
        self._clear_body()
        self._set_actions_enabled(False)
        wrap = ttk.Frame(self.body, style="App.TFrame")
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        card = ttk.Frame(wrap, style="Card.TFrame", padding=(40, 34))
        card.pack()
        ttk.Label(card, text=title, style="CardH2.TLabel").pack()
        ttk.Label(card, text=message, style="CardMuted.TLabel", justify="center",
                  wraplength=420).pack(pady=(10, 18))
        ttk.Button(card, text="Open a file…", style="Primary.TButton",
                   command=self.open_dialog).pack()
        self.status_var.set("Ready.")

    def _show_results(self):
        self._clear_body()
        self._set_actions_enabled(True)

        container = ttk.Frame(self.body, style="App.TFrame", padding=(16, 14))
        container.pack(fill="both", expand=True)

        # thumbnail rail (only when there's more than one label)
        if len(self.labels) > 1:
            self._build_rail(container)

        # preview + info column
        main = ttk.Frame(container, style="App.TFrame")
        main.pack(side="left", fill="both", expand=True)

        self.preview_canvas = tk.Canvas(main, background=theme.CANVAS_BG,
                                        highlightthickness=1,
                                        highlightbackground=theme.BORDER)
        self.preview_canvas.pack(side="top", fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)

        self.info_bar = ttk.Frame(main, style="App.TFrame", padding=(2, 12, 2, 0))
        self.info_bar.pack(side="top", fill="x")

        self._render_selected()

    def _build_rail(self, parent):
        rail_wrap = ttk.Frame(parent, style="Rail.TFrame", padding=(8, 8))
        rail_wrap.pack(side="left", fill="y", padx=(0, 14))
        ttk.Label(rail_wrap, text=f"{len(self.labels)} labels",
                  background=theme.RAIL_BG, foreground=theme.INK_SOFT,
                  font=self.fonts.small).pack(anchor="w", pady=(0, 6))

        canvas = tk.Canvas(rail_wrap, width=132, background=theme.RAIL_BG,
                           highlightthickness=0)
        canvas.pack(side="left", fill="y", expand=True)
        inner = ttk.Frame(canvas, style="Rail.TFrame")
        canvas.create_window((0, 0), window=inner, anchor="nw")

        self._thumb_photos = []
        self._thumb_frames = []
        for i, lab in enumerate(self.labels):
            self._thumb_photos.append(self._make_thumb(lab))
            cell = tk.Frame(inner, background=theme.RAIL_BG,
                            highlightthickness=2,
                            highlightbackground=theme.RAIL_BG)
            cell.pack(pady=5)
            lbl = tk.Label(cell, image=self._thumb_photos[-1], background=theme.SURFACE,
                           borderwidth=0, cursor="hand2")
            lbl.pack()
            cap = tk.Label(cell, text=f"Page {lab.page_index + 1}", background=theme.RAIL_BG,
                           foreground=theme.INK_SOFT, font=self.fonts.tiny)
            cap.pack()
            for widget in (lbl, cap, cell):
                widget.bind("<Button-1>", lambda e, idx=i: self._select(idx))
            self._thumb_frames.append(cell)

        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        self._rail_canvas = canvas
        self._highlight_rail()

        def _wheel(event):
            delta = 1 if getattr(event, "num", None) == 5 or event.delta < 0 else -1
            canvas.yview_scroll(delta, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind(seq, _wheel)
            inner.bind(seq, _wheel)

    def _make_thumb(self, lab: ProcessedLabel) -> ImageTk.PhotoImage:
        img = lab.preview_image
        tw = 116
        scale = min(1.0, tw / img.width)
        th = max(1, int(img.height * scale))
        th = min(th, 150)
        scale = min(tw / img.width, th / img.height)
        disp = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                          Image.LANCZOS)
        return ImageTk.PhotoImage(disp)

    def _highlight_rail(self):
        if not hasattr(self, "_thumb_frames"):
            return
        for i, cell in enumerate(self._thumb_frames):
            color = theme.ORANGE if i == self.selected else theme.RAIL_BG
            cell.configure(highlightbackground=color, highlightcolor=color)

    # -- rendering the selected label -----------------------------------------
    def _render_selected(self):
        if not self.labels:
            return
        self._render_preview()
        self._render_info()
        self._highlight_rail()

    def _on_preview_resize(self, event):
        size = (event.width, event.height)
        if size != self._last_preview_size:
            self._last_preview_size = size
            self._render_preview()

    def _render_preview(self):
        if not self.labels or not hasattr(self, "preview_canvas"):
            return
        canvas = self.preview_canvas
        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        canvas.delete("all")
        img = self.labels[self.selected].preview_image
        pad = 24
        avail_w = max(10, cw - pad * 2)
        avail_h = max(10, ch - pad * 2)
        scale = min(avail_w / img.width, avail_h / img.height)
        dw = max(1, int(img.width * scale))
        dh = max(1, int(img.height * scale))
        disp = img.resize((dw, dh), Image.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(disp)
        cx, cy = cw // 2, ch // 2
        # soft drop shadow + white paper
        canvas.create_rectangle(cx - dw // 2 + 4, cy - dh // 2 + 5,
                                cx + dw // 2 + 4, cy + dh // 2 + 5,
                                fill="#D9D3CA", outline="")
        canvas.create_rectangle(cx - dw // 2 - 1, cy - dh // 2 - 1,
                                cx + dw // 2 + 1, cy + dh // 2 + 1,
                                fill=theme.SURFACE, outline=theme.BORDER_STRONG)
        canvas.create_image(cx, cy, image=self._preview_photo)

    def _render_info(self):
        for w in self.info_bar.winfo_children():
            w.destroy()
        lab = self.labels[self.selected]
        r = lab.result

        text, fg, bg = theme.confidence_style(r.confidence)
        badge = tk.Label(self.info_bar, text=f"  {text} · {int(r.confidence * 100)}%  ",
                         background=bg, foreground=fg, font=self.fonts.badge,
                         padx=6, pady=3)
        badge.pack(side="left")

        # dimensions in inches at print dpi
        pimg = lab.preview_image
        w_in = pimg.width / r.detect_dpi
        h_in = pimg.height / r.detect_dpi
        dims = f"{w_in:.1f}″ × {h_in:.1f}″"
        ttk.Label(self.info_bar, text=dims, style="Muted.TLabel").pack(side="left", padx=(12, 0))

        if lab.page_count > 1:
            ttk.Label(self.info_bar, text=f"Page {lab.page_index + 1} of {lab.page_count}",
                      style="Muted.TLabel").pack(side="left", padx=(12, 0))

        if lab.manual_rotation:
            ttk.Label(self.info_bar, text=f"Rotated {lab.manual_rotation}°",
                      style="Muted.TLabel").pack(side="left", padx=(12, 0))

        if r.warnings:
            warn = tk.Label(self.info_bar, text="⚠  " + r.warnings[0],
                            background=theme.BG, foreground=theme.AMBER,
                            font=self.fonts.small, wraplength=420, justify="left")
            warn.pack(side="right")

    # -- actions ---------------------------------------------------------------
    def _set_actions_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for name in ("btn_rot_ccw", "btn_rot_cw", "btn_save", "btn_print"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.configure(state=state)

    def _select(self, idx: int):
        if 0 <= idx < len(self.labels):
            self.selected = idx
            self._render_selected()

    def _select_delta(self, delta: int):
        if self.labels:
            self._select((self.selected + delta) % len(self.labels))

    def _rotate(self, clockwise: bool):
        if not self.labels:
            return
        lab = self.labels[self.selected]
        lab.rotate_cw() if clockwise else lab.rotate_ccw()
        # refresh this label's thumbnail too
        if hasattr(self, "_thumb_frames") and len(self.labels) > 1:
            self._thumb_photos[self.selected] = self._make_thumb(lab)
            cell = self._thumb_frames[self.selected]
            for child in cell.winfo_children():
                if isinstance(child, tk.Label) and child.cget("image"):
                    child.configure(image=self._thumb_photos[self.selected])
                    break
        self._render_selected()

    def _refresh_printers(self):
        try:
            printers = list_printers()
        except Exception:
            printers = []
        if self.config.printer and self.config.printer not in printers:
            printers = [self.config.printer] + printers
        self.printer_combo.configure(values=printers)
        if self.config.printer:
            self.printer_var.set(self.config.printer)
        elif printers:
            self.printer_var.set(printers[0])

    def _on_printer_change(self, _event=None):
        self.config.printer = self.printer_var.get()
        try:
            self.config.save()
        except Exception:
            pass

    def _sync_copies(self):
        try:
            self.config.copies = max(1, min(99, int(self.copies_var.get())))
        except (ValueError, tk.TclError):
            self.config.copies = 1
            self.copies_var.set("1")

    # -- file input ------------------------------------------------------------
    def open_dialog(self):
        path = filedialog.askopenfilename(
            title="Choose a shipping label",
            filetypes=[("Label files", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                       ("PDF", "*.pdf"), ("Images", "*.png *.jpg *.jpeg"),
                       ("All files", "*.*")])
        if path:
            self.load_path(path)

    def paste_from_clipboard(self):
        try:
            from printpal.clipboard import get_pdf_path
            path = get_pdf_path()
        except Exception:
            path = None
        if path:
            self.load_path(path)
        else:
            self.status_var.set("Clipboard has no label file. Copy a label, then paste.")

    def load_path(self, path: str):
        if self._busy:
            return
        err = _validate_file(path)
        if err:
            self._show_error("That file won't work", err)
            return
        self._busy = True
        self._show_loading("Reading your label…")
        self.status_var.set(f"Processing {os.path.basename(path)}…")
        self._start_progress()
        worker = threading.Thread(target=self._process_worker, args=(path,), daemon=True)
        worker.start()

    def _process_worker(self, path: str):
        def progress(msg, i, n):
            self._queue.put(("status", msg))
        try:
            labels = process_file(path, self.config, progress=progress)
            self._queue.put(("done", labels))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self._queue.put(("error", str(exc)))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._on_processing_done(payload)
                elif kind == "error":
                    self._busy = False
                    self._stop_progress()
                    self._show_error("Something went wrong",
                                     f"PrintPal couldn't read that label.\n\n{payload}")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _on_processing_done(self, labels: list[ProcessedLabel]):
        self._busy = False
        self._stop_progress()
        self.labels = labels
        self.selected = 0
        if not labels or not labels[0].is_printable:
            self._show_error("No label found",
                             "This file doesn't seem to contain anything to print.")
            return
        self._show_results()
        printable = len(labels)
        best = labels[0].result
        self.status_var.set(
            f"Found {printable} label{'s' if printable != 1 else ''} · "
            f"{int(best.confidence * 100)}% confident")
        if self._on_ready:
            self._on_ready(self)

    # -- printing --------------------------------------------------------------
    def _print_current(self):
        if not self.labels or self._busy:
            return
        self._sync_copies()
        self.config.printer = self.printer_var.get()
        lab = self.labels[self.selected]
        self.status_var.set("Rendering at print quality…")
        self.root.update_idletasks()
        try:
            image = lab.render_print_image(self.config)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("PrintPal", f"Couldn't render the label.\n\n{exc}",
                                 parent=self.root)
            self.status_var.set("Ready.")
            return
        try:
            from printpal.printing import print_label
            print_label(image, self.config.printer, copies=self.config.copies)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("PrintPal – print error", str(exc), parent=self.root)
            self.status_var.set("Print failed.")
            return
        copies = self.config.copies
        self.status_var.set(
            f"Sent to {self.config.printer}"
            + (f" × {copies}" if copies > 1 else "") + ".")
        try:
            self.config.save()
        except Exception:
            pass

    def _save_current(self):
        if not self.labels:
            return
        lab = self.labels[self.selected]
        path = filedialog.asksaveasfilename(
            title="Save cropped label",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
        if not path:
            return
        try:
            image = lab.render_print_image(self.config)
            image.save(path)
            self.status_var.set(f"Saved {os.path.basename(path)}.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("PrintPal", f"Couldn't save.\n\n{exc}", parent=self.root)

    def open_settings(self):
        SettingsDialog(self.root, self.config, on_save=self._after_settings)

    def _after_settings(self):
        self._refresh_printers()
        self.copies_var.set(str(self.config.copies))
        # crop/dpi changes affect detection -- offer to re-run on the same file
        if self.labels:
            path = self.labels[0].source_path
            self.load_path(path)

    # -- progress bar in status --------------------------------------------
    def _start_progress(self):
        self.status_progress.pack(side="right")
        self.status_progress.start(12)

    def _stop_progress(self):
        try:
            self.status_progress.stop()
            self.status_progress.pack_forget()
        except tk.TclError:
            pass

    def _center(self):
        self.root.update_idletasks()
        w = self.root.winfo_width() or 980
        h = self.root.winfo_height() or 680
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def run(self):
        self.root.mainloop()


class SettingsDialog:
    """Modal settings: printer, media, DPI, crop margin, auto-print."""

    def __init__(self, parent: tk.Tk, config: Config, on_save: callable | None = None):
        self.config = config
        self.on_save = on_save
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("PrintPal Settings")
        self.dialog.transient(parent)
        self.dialog.configure(background=theme.BG)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        self.fonts = theme.Fonts(self.dialog)

        self._build()
        self.dialog.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width() // 2
        py = parent.winfo_y() + parent.winfo_height() // 2
        w = self.dialog.winfo_width()
        h = self.dialog.winfo_height()
        self.dialog.geometry(f"+{px - w // 2}+{py - h // 2}")

    def _build(self):
        main = ttk.Frame(self.dialog, style="App.TFrame", padding=22)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="Settings", style="H1.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        def row(r, label):
            ttk.Label(main, text=label, style="TLabel").grid(
                row=r, column=0, sticky="w", pady=6, padx=(0, 14))

        row(1, "Printer")
        try:
            printers = list_printers()
        except Exception:
            printers = []
        if self.config.printer and self.config.printer not in printers:
            printers = [self.config.printer] + printers
        self.printer_var = tk.StringVar(value=self.config.printer)
        ttk.Combobox(main, textvariable=self.printer_var, values=printers,
                     state="readonly", width=30).grid(row=1, column=1, sticky="ew", pady=6)

        row(2, "Media size")
        self.media_var = tk.StringVar(value=self.config.media_size)
        ttk.Combobox(main, textvariable=self.media_var,
                     values=["4x6", "4x8", "2.25x1.25", "2.25x4", "4x2", "6x4"],
                     width=30).grid(row=2, column=1, sticky="ew", pady=6)

        row(3, "Detection DPI")
        self.detect_var = tk.StringVar(value=str(self.config.detect_dpi))
        ttk.Spinbox(main, from_=100, to=400, increment=10, width=8,
                    textvariable=self.detect_var).grid(row=3, column=1, sticky="w", pady=6)

        row(4, "Print DPI")
        self.print_var = tk.StringVar(value=str(self.config.print_dpi))
        ttk.Spinbox(main, from_=150, to=600, increment=50, width=8,
                    textvariable=self.print_var).grid(row=4, column=1, sticky="w", pady=6)

        row(5, "Crop margin (in)")
        self.margin_var = tk.StringVar(value=str(self.config.crop_margin_inches))
        ttk.Spinbox(main, from_=0.0, to=0.5, increment=0.02, width=8,
                    textvariable=self.margin_var).grid(row=5, column=1, sticky="w", pady=6)

        self.auto_var = tk.BooleanVar(value=self.config.auto_print)
        ttk.Checkbutton(main, text="Auto-print when confidence is high",
                        variable=self.auto_var, style="TCheckbutton").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

        btns = ttk.Frame(main, style="App.TFrame")
        btns.grid(row=7, column=0, columnspan=2, pady=(20, 0), sticky="e")
        ttk.Button(btns, text="Cancel", style="Ghost.TButton",
                   command=self.dialog.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Save", style="Primary.TButton",
                   command=self._save).pack(side="right")
        main.columnconfigure(1, weight=1)

    def _save(self):
        self.config.printer = self.printer_var.get()
        self.config.media_size = self.media_var.get()
        for var, attr, cast, err in (
            (self.detect_var, "detect_dpi", int, "Detection DPI must be a whole number."),
            (self.print_var, "print_dpi", int, "Print DPI must be a whole number."),
            (self.margin_var, "crop_margin_inches", float, "Crop margin must be a number."),
        ):
            try:
                setattr(self.config, attr, cast(var.get()))
            except ValueError:
                messagebox.showerror("Invalid value", err, parent=self.dialog)
                return
        self.config.auto_print = bool(self.auto_var.get())
        self.config.clamped()
        try:
            self.config.save()
        except Exception:
            pass
        self.dialog.destroy()
        if self.on_save:
            self.on_save()


class PrinterPicker:
    """Modal shown when the configured printer isn't found."""

    def __init__(self, printers: list[str], current: str, message: str):
        self.result: str | None = None
        self.root = tk.Tk()
        self.root.title("PrintPal – Choose printer")
        self.root.resizable(False, False)
        self.style, self.fonts = theme.apply_theme(self.root)
        self.root.attributes("-topmost", True)

        main = ttk.Frame(self.root, style="App.TFrame", padding=22)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="Choose a printer", style="H1.TLabel").pack(anchor="w")
        ttk.Label(main, text=message, style="Muted.TLabel", wraplength=380,
                  justify="left").pack(anchor="w", pady=(6, 12))

        self.listbox = tk.Listbox(main, height=min(10, max(3, len(printers))),
                                  font=self.fonts.body, selectmode="browse",
                                  activestyle="none", highlightthickness=1,
                                  highlightbackground=theme.BORDER_STRONG,
                                  selectbackground=theme.ORANGE,
                                  selectforeground="#FFFFFF", borderwidth=0)
        for p in printers:
            self.listbox.insert("end", p)
        if printers:
            self.listbox.selection_set(0)
        self.listbox.pack(fill="x", pady=(0, 14))

        btns = ttk.Frame(main, style="App.TFrame")
        btns.pack(fill="x")
        ttk.Button(btns, text="Use this printer", style="Primary.TButton",
                   command=self._select).pack(side="right")
        ttk.Button(btns, text="Cancel", style="Ghost.TButton",
                   command=self._cancel).pack(side="right", padx=(0, 8))

        self.listbox.bind("<Double-1>", lambda e: self._select())
        self.root.bind("<Return>", lambda e: self._select())
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")

    def _select(self):
        sel = self.listbox.curselection()
        if sel:
            self.result = self.listbox.get(sel[0])
        self.root.destroy()

    def _cancel(self):
        self.result = None
        self.root.destroy()

    def run(self) -> str | None:
        self.root.mainloop()
        return self.result


def _validate_file(path: str) -> str | None:
    """Return a friendly error string if the file can't be used, else None."""
    if not path or not os.path.isfile(path):
        return "That file could not be found."
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"):
        return f"PrintPal works with PDF and image files, not {ext or 'this type'}."
    if ext == ".pdf":
        try:
            with open(path, "rb") as f:
                if not f.read(5).startswith(b"%PDF"):
                    return "This file has a .pdf name but isn't a valid PDF."
        except OSError as e:
            return f"Cannot read the file: {e}"
    return None


def show_error(title: str, message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def show_info(title: str, message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(title, message)
    root.destroy()
