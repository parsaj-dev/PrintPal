"""Visual design system for PrintPal's UI.

A small, warm palette built around the app's orange-and-brown brand mark, plus a
`ttk` theme that turns tkinter's dated default widgets into something that looks
deliberate. Everything is defined once here so the windows in `ui.py` stay about
layout, not styling.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# -- palette ------------------------------------------------------------------
ORANGE = "#FF8C32"          # brand
ORANGE_DARK = "#EB7818"     # hover / pressed
ORANGE_DEEP = "#CE6410"     # active
ORANGE_SOFT = "#FFF1E3"     # tinted fills
BROWN = "#50321E"           # brand dark
INK = "#2B2620"             # primary text
INK_SOFT = "#7A7167"        # secondary text
BG = "#F4F1ED"              # app background (warm grey)
SURFACE = "#FFFFFF"         # cards
SURFACE_ALT = "#FBF9F6"     # subtle alt surface
BORDER = "#E5DFD6"          # hairlines
BORDER_STRONG = "#D6CEC2"
CANVAS_BG = "#EAE5DE"       # preview backdrop
RAIL_BG = "#EFEBE5"         # thumbnail rail

GREEN = "#237A48"
GREEN_SOFT = "#E4F2E9"
AMBER = "#B9770B"
AMBER_SOFT = "#FBEED5"
RED = "#BE3A2B"
RED_SOFT = "#F8E4E1"

_PREFERRED_FAMILIES = ("Segoe UI", "Selawik", "Helvetica Neue", "Arial", "DejaVu Sans")


def pick_family(root: tk.Misc) -> str:
    available = set(tkfont.families(root))
    for fam in _PREFERRED_FAMILIES:
        if fam in available:
            return fam
    return "TkDefaultFont"


class Fonts:
    def __init__(self, root: tk.Misc):
        fam = pick_family(root)
        self.family = fam
        self.wordmark = tkfont.Font(root=root, family=fam, size=17, weight="bold")
        self.h1 = tkfont.Font(root=root, family=fam, size=16, weight="bold")
        self.h2 = tkfont.Font(root=root, family=fam, size=12, weight="bold")
        self.body = tkfont.Font(root=root, family=fam, size=10)
        self.body_bold = tkfont.Font(root=root, family=fam, size=10, weight="bold")
        self.small = tkfont.Font(root=root, family=fam, size=9)
        self.badge = tkfont.Font(root=root, family=fam, size=10, weight="bold")
        self.button = tkfont.Font(root=root, family=fam, size=10, weight="bold")
        self.tiny = tkfont.Font(root=root, family=fam, size=8)


def apply_theme(root: tk.Misc) -> tuple[ttk.Style, Fonts]:
    """Install the PrintPal ttk theme. Returns (style, fonts)."""
    fonts = Fonts(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=INK, font=fonts.body,
                    focuscolor=BG, borderwidth=0)

    # Frames / surfaces
    style.configure("App.TFrame", background=BG)
    style.configure("Header.TFrame", background=ORANGE)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Alt.TFrame", background=SURFACE_ALT)
    style.configure("Rail.TFrame", background=RAIL_BG)
    style.configure("Status.TFrame", background=SURFACE_ALT)
    style.configure("Sep.TFrame", background=BORDER)

    # Labels
    style.configure("TLabel", background=BG, foreground=INK, font=fonts.body)
    style.configure("Card.TLabel", background=SURFACE, foreground=INK, font=fonts.body)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=INK_SOFT, font=fonts.small)
    style.configure("Muted.TLabel", background=BG, foreground=INK_SOFT, font=fonts.small)
    style.configure("H1.TLabel", background=BG, foreground=INK, font=fonts.h1)
    style.configure("CardH2.TLabel", background=SURFACE, foreground=INK, font=fonts.h2)
    style.configure("Wordmark.TLabel", background=ORANGE, foreground="#FFFFFF",
                    font=fonts.wordmark)
    style.configure("HeaderSub.TLabel", background=ORANGE, foreground="#FFF7EF",
                    font=fonts.small)
    style.configure("Status.TLabel", background=SURFACE_ALT, foreground=INK_SOFT,
                    font=fonts.small)

    # Primary button (orange call to action)
    style.configure("Primary.TButton", background=ORANGE, foreground="#FFFFFF",
                    font=fonts.button, borderwidth=0, focusthickness=0,
                    padding=(18, 10), relief="flat")
    style.map("Primary.TButton",
              background=[("disabled", "#F0C8A2"), ("pressed", ORANGE_DEEP),
                          ("active", ORANGE_DARK)],
              foreground=[("disabled", "#FFFFFF")])

    # Secondary / ghost button
    style.configure("Ghost.TButton", background=SURFACE, foreground=INK,
                    font=fonts.button, borderwidth=1, relief="flat",
                    bordercolor=BORDER_STRONG, padding=(14, 9))
    style.map("Ghost.TButton",
              background=[("pressed", ORANGE_SOFT), ("active", SURFACE_ALT)],
              bordercolor=[("active", ORANGE)])

    # Header ghost button (sits on orange)
    style.configure("HeaderGhost.TButton", background=ORANGE, foreground="#FFFFFF",
                    font=fonts.button, borderwidth=1, relief="flat",
                    bordercolor="#FFC38A", padding=(12, 7))
    style.map("HeaderGhost.TButton",
              background=[("pressed", ORANGE_DEEP), ("active", ORANGE_DARK)],
              bordercolor=[("active", "#FFFFFF")])

    # Small icon-ish tool button
    style.configure("Tool.TButton", background=SURFACE, foreground=INK,
                    font=fonts.button, borderwidth=1, relief="flat",
                    bordercolor=BORDER_STRONG, padding=(10, 8))
    style.map("Tool.TButton",
              background=[("pressed", ORANGE_SOFT), ("active", SURFACE_ALT)],
              bordercolor=[("active", ORANGE)])

    # Combobox / spinbox
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE,
                    foreground=INK, arrowcolor=INK, bordercolor=BORDER_STRONG,
                    lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
              bordercolor=[("focus", ORANGE)])
    style.configure("TSpinbox", fieldbackground=SURFACE, background=SURFACE,
                    foreground=INK, arrowcolor=INK, bordercolor=BORDER_STRONG,
                    padding=4)
    style.map("TSpinbox", bordercolor=[("focus", ORANGE)])

    style.configure("TEntry", fieldbackground=SURFACE, foreground=INK,
                    bordercolor=BORDER_STRONG, padding=4)
    style.map("TEntry", bordercolor=[("focus", ORANGE)])

    style.configure("Brand.Horizontal.TProgressbar", background=ORANGE,
                    troughcolor=ORANGE_SOFT, borderwidth=0, thickness=6)
    style.configure("TCheckbutton", background=SURFACE, foreground=INK, font=fonts.body)
    style.map("TCheckbutton", background=[("active", SURFACE)])

    try:
        root.configure(background=BG)
    except tk.TclError:
        pass
    return style, fonts


def confidence_style(confidence: float) -> tuple[str, str, str]:
    """Return (label_text, fg_color, soft_bg_color) for a confidence value."""
    if confidence >= 0.85:
        return "Looks great", GREEN, GREEN_SOFT
    if confidence >= 0.5:
        return "Worth a look", AMBER, AMBER_SOFT
    return "Please review", RED, RED_SOFT
