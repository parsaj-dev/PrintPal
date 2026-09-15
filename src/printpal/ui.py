"""Tkinter UI for label preview, settings, and notifications.

Kept lightweight for fast startup on older hardware. Uses only stdlib tkinter.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

from printpal.config import Config
from printpal.detect import LabelResult
from printpal.printing import list_printers


class PreviewWindow:
    """Shows the cropped label and lets the user print or cancel."""

    def __init__(self, result: LabelResult, config: Config, on_print: callable, on_cancel: callable):
        self.result = result
        self.config = config
        self.on_print = on_print
        self.on_cancel = on_cancel
        self._printed = False

        self.root = tk.Tk()
        self.root.title("PrintPal - Label Preview")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._build()
        self._center()
        self.root.bind("<Return>", lambda e: self._do_print())
        self.root.bind("<Escape>", lambda e: self._do_cancel())
        self.root.protocol("WM_DELETE_WINDOW", self._do_cancel)

    def _build(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        # scale preview to fit a reasonable window (max 480px tall)
        img = self.result.image
        max_h = 480
        scale = min(1.0, max_h / img.height)
        disp_w = int(img.width * scale)
        disp_h = int(img.height * scale)
        preview = img.resize((disp_w, disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(preview)

        canvas = tk.Label(main, image=self._photo, borderwidth=1, relief="solid")
        canvas.pack(pady=(0, 8))

        # info bar
        info = ttk.Frame(main)
        info.pack(fill="x", pady=(0, 8))

        conf_pct = int(self.result.confidence * 100)
        color = "#2e7d32" if self.result.confidence >= 0.7 else "#e65100"
        conf_label = tk.Label(info, text=f"Confidence: {conf_pct}%", fg=color,
                              font=("Segoe UI", 10, "bold"))
        conf_label.pack(side="left")

        printer_label = ttk.Label(info, text=f"Printer: {self.config.printer}",
                                   font=("Segoe UI", 9))
        printer_label.pack(side="right")

        # warnings
        if self.result.warnings:
            for w in self.result.warnings:
                wl = tk.Label(main, text=f"Warning: {w}", fg="#e65100",
                              font=("Segoe UI", 9), wraplength=disp_w, justify="left")
                wl.pack(fill="x", pady=(0, 2))

        # size info
        w_in = self.result.image.width / 300  # approximate
        h_in = self.result.image.height / 300
        size_label = ttk.Label(main, text=f"Label: {self.result.image.width}x{self.result.image.height} px  (~{w_in:.1f}x{h_in:.1f} in)",
                                font=("Segoe UI", 9))
        size_label.pack(fill="x", pady=(0, 8))

        # buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x")

        self.print_btn = ttk.Button(btn_frame, text="Print (Enter)", command=self._do_print)
        self.print_btn.pack(side="right", padx=(4, 0))

        cancel_btn = ttk.Button(btn_frame, text="Cancel (Esc)", command=self._do_cancel)
        cancel_btn.pack(side="right")

        settings_btn = ttk.Button(btn_frame, text="Settings...", command=self._open_settings)
        settings_btn.pack(side="left")

        if self.result.confidence < 0.5:
            self.print_btn.configure(state="disabled")

    def _center(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _do_print(self):
        if self._printed:
            return
        self._printed = True
        self.print_btn.configure(state="disabled")
        try:
            self.on_print()
        finally:
            self.root.after(300, self.root.destroy)

    def _do_cancel(self):
        self.on_cancel()
        self.root.destroy()

    def _open_settings(self):
        SettingsDialog(self.root, self.config)

    def run(self):
        self.root.mainloop()


class SettingsDialog:
    """Modal settings window for printer selection and crop parameters."""

    def __init__(self, parent: tk.Tk, config: Config):
        self.config = config
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("PrintPal Settings")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)

        self._build()
        self.dialog.update_idletasks()
        pw = parent.winfo_x() + parent.winfo_width() // 2
        ph = parent.winfo_y() + parent.winfo_height() // 2
        w = self.dialog.winfo_width()
        h = self.dialog.winfo_height()
        self.dialog.geometry(f"+{pw - w // 2}+{ph - h // 2}")

    def _build(self):
        main = ttk.Frame(self.dialog, padding=16)
        main.pack(fill="both", expand=True)

        # printer
        ttk.Label(main, text="Printer:", font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=4)
        printers = list_printers()
        self.printer_var = tk.StringVar(value=self.config.printer)
        printer_combo = ttk.Combobox(main, textvariable=self.printer_var, values=printers,
                                      state="readonly", width=32)
        printer_combo.grid(row=0, column=1, padx=(8, 0), pady=4)

        # media size
        ttk.Label(main, text="Media size:", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=4)
        self.media_var = tk.StringVar(value=self.config.media_size)
        media_combo = ttk.Combobox(main, textvariable=self.media_var,
                                    values=["4x6", "2.25x1.25", "2.25x4", "4x2"],
                                    width=32)
        media_combo.grid(row=1, column=1, padx=(8, 0), pady=4)

        # DPI
        ttk.Label(main, text="Rasterize DPI:", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=4)
        self.dpi_var = tk.StringVar(value=str(self.config.dpi))
        dpi_entry = ttk.Entry(main, textvariable=self.dpi_var, width=8)
        dpi_entry.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=4)

        # crop margin
        ttk.Label(main, text="Crop margin (in):", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=4)
        self.margin_var = tk.StringVar(value=str(self.config.crop_margin_inches))
        margin_entry = ttk.Entry(main, textvariable=self.margin_var, width=8)
        margin_entry.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=4)

        # buttons
        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(12, 0))

        ttk.Button(btn_frame, text="Save", command=self._save).pack(side="right", padx=(4, 0))
        ttk.Button(btn_frame, text="Cancel", command=self.dialog.destroy).pack(side="right")

    def _save(self):
        self.config.printer = self.printer_var.get()
        self.config.media_size = self.media_var.get()
        try:
            self.config.dpi = int(self.dpi_var.get())
        except ValueError:
            messagebox.showerror("Invalid DPI", "DPI must be a whole number.", parent=self.dialog)
            return
        try:
            self.config.crop_margin_inches = float(self.margin_var.get())
        except ValueError:
            messagebox.showerror("Invalid margin", "Margin must be a number.", parent=self.dialog)
            return
        self.config.save()
        self.dialog.destroy()


class PrinterPicker:
    """Modal dialog that shows all installed printers and lets the user pick one.
    Shown when the configured printer is not found."""

    def __init__(self, printers: list[str], current: str, message: str):
        self.result: str | None = None

        self.root = tk.Tk()
        self.root.title("PrintPal - Choose Printer")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text=message, wraplength=400, justify="left",
                  font=("Segoe UI", 10)).pack(pady=(0, 12))

        ttk.Label(main, text="Select a printer:", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.printer_var = tk.StringVar(value=printers[0] if printers else "")
        self.listbox = tk.Listbox(main, height=min(10, max(3, len(printers))),
                                   font=("Segoe UI", 10), selectmode="browse")
        for p in printers:
            self.listbox.insert("end", p)
        if printers:
            self.listbox.selection_set(0)
        self.listbox.pack(fill="x", pady=(4, 12))

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x")

        ttk.Button(btn_frame, text="Use this printer", command=self._select).pack(side="right", padx=(4, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side="right")

        self.listbox.bind("<Double-1>", lambda e: self._select())
        self.root.bind("<Return>", lambda e: self._select())
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)

        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

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


def show_error(title: str, message: str) -> None:
    """Show a simple error dialog and return."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def show_info(title: str, message: str) -> None:
    """Show a quick info popup and return."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(title, message)
    root.destroy()


class ProgressWindow:
    """Small always-on-top window showing processing status."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PrintPal")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        frame = ttk.Frame(self.root, padding=20)
        frame.pack()

        self._title = ttk.Label(frame, text="PrintPal", font=("Segoe UI", 11, "bold"))
        self._title.pack(pady=(0, 8))

        self._label = ttk.Label(frame, text="Starting...", font=("Segoe UI", 10),
                                width=30, anchor="center")
        self._label.pack()

        self._bar = ttk.Progressbar(frame, mode="indeterminate", length=220)
        self._bar.pack(pady=(8, 0))
        self._bar.start(15)

        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def update(self, text: str):
        self._label.configure(text=text)
        self.root.update()

    def close(self):
        try:
            self._bar.stop()
            self.root.destroy()
        except tk.TclError:
            pass
