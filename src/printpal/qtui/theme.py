"""Visual design system for the Qt UI: palettes, fonts and a QSS stylesheet.

Two palettes (light / dark) around the orange-and-brown brand. All colour lives
here so the widgets stay about layout. `stylesheet(dark)` returns the full QSS.
"""
from __future__ import annotations

from dataclasses import dataclass

# Brand -- constant across themes.
BRAND = "#F5852A"
BRAND_HOVER = "#E5761A"
BRAND_PRESS = "#CE6410"
BROWN = "#4A2F1C"

# Confidence colours (tuned per theme below via Palette).
GREEN = "#1F8A4C"
AMBER = "#C1820A"
RED = "#C6402F"


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    brand_tint: str
    canvas: str
    chip_bg: str
    green: str
    green_tint: str
    amber: str
    amber_tint: str
    red: str
    red_tint: str


LIGHT = Palette(
    dark=False,
    bg="#F6F3EF", surface="#FFFFFF", surface_alt="#FBF8F4",
    border="#E7E1D8", border_strong="#D8CFC2",
    text="#2A2620", text_muted="#8A8073",
    brand_tint="#FFF1E3", canvas="#ECE7DF", chip_bg="#F0EBE3",
    green=GREEN, green_tint="#E4F3EA",
    amber=AMBER, amber_tint="#FBEFD4",
    red=RED, red_tint="#F8E5E1",
)

DARK = Palette(
    dark=True,
    bg="#1B1815", surface="#252119", surface_alt="#2C2720",
    border="#39332B", border_strong="#4A4238",
    text="#F2EDE6", text_muted="#A79E90",
    brand_tint="#3A2A18", canvas="#161310", chip_bg="#302A22",
    green="#4FB37A", green_tint="#1E3A2A",
    amber="#E0A93B", amber_tint="#3A2E14",
    red="#E4705F", red_tint="#3A211C",
)


def palette(dark: bool) -> Palette:
    return DARK if dark else LIGHT


def confidence_style(confidence: float, p: Palette) -> tuple[str, str, str]:
    """(label, fg, tint-bg) for a confidence value."""
    if confidence >= 0.85:
        return "Looks great", p.green, p.green_tint
    if confidence >= 0.5:
        return "Worth a look", p.amber, p.amber_tint
    return "Please review", p.red, p.red_tint


FONT_STACK = '"Segoe UI Variable", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'


def stylesheet(dark: bool) -> str:
    p = palette(dark)
    return f"""
    * {{
        font-family: {FONT_STACK};
        color: {p.text};
        outline: 0;
    }}
    QWidget#Root {{ background: {p.bg}; }}
    QWidget {{ background: transparent; font-size: 14px; }}

    /* Header */
    QFrame#Header {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
    QLabel#Wordmark {{ font-size: 20px; font-weight: 700; color: {p.text}; }}
    QLabel#Tagline {{ font-size: 12px; color: {p.text_muted}; }}

    /* Nav segmented control */
    QPushButton#NavBtn {{
        background: transparent; border: none; padding: 8px 16px;
        color: {p.text_muted}; font-weight: 600; font-size: 13px;
        border-radius: 9px;
    }}
    QPushButton#NavBtn:hover {{ background: {p.surface_alt}; color: {p.text}; }}
    QPushButton#NavBtn:checked {{ background: {p.brand_tint}; color: {BRAND_PRESS if not dark else BRAND}; }}

    /* Cards */
    QFrame#Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 16px; }}
    QFrame#Canvas {{ background: {p.canvas}; border: 1px solid {p.border}; border-radius: 16px; }}
    QLabel#H1 {{ font-size: 20px; font-weight: 700; }}
    QLabel#H2 {{ font-size: 15px; font-weight: 700; }}
    QLabel#Muted {{ color: {p.text_muted}; font-size: 13px; }}
    QLabel#Tiny {{ color: {p.text_muted}; font-size: 11px; }}

    /* Buttons */
    QPushButton {{
        background: {p.surface}; border: 1px solid {p.border_strong};
        border-radius: 10px; padding: 8px 16px; font-weight: 600; font-size: 13px;
        color: {p.text};
    }}
    QPushButton:hover {{ border-color: {BRAND}; }}
    QPushButton:disabled {{ color: {p.text_muted}; border-color: {p.border}; }}

    QPushButton#Primary {{
        background: {BRAND}; color: #FFFFFF; border: none; padding: 11px 22px;
        border-radius: 11px; font-size: 14px; font-weight: 700;
    }}
    QPushButton#Primary:hover {{ background: {BRAND_HOVER}; }}
    QPushButton#Primary:pressed {{ background: {BRAND_PRESS}; }}
    QPushButton#Primary:disabled {{ background: {p.border_strong}; color: {p.surface}; }}

    QPushButton#Tool {{ padding: 8px 12px; border-radius: 10px; }}
    QPushButton#Ghost {{ background: transparent; border: 1px solid {p.border_strong}; }}
    QPushButton#Ghost:hover {{ background: {p.surface_alt}; border-color: {BRAND}; }}
    QPushButton#IconBtn {{ background: transparent; border: none; padding: 6px 10px;
        border-radius: 9px; color: {p.text_muted}; font-size: 15px; }}
    QPushButton#IconBtn:hover {{ background: {p.surface_alt}; color: {p.text}; }}

    /* Inputs */
    QComboBox, QSpinBox, QLineEdit {{
        background: {p.surface}; border: 1px solid {p.border_strong};
        border-radius: 9px; padding: 7px 10px; min-height: 18px; color: {p.text};
    }}
    QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {BRAND}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface}; border: 1px solid {p.border_strong};
        selection-background-color: {p.brand_tint}; selection-color: {p.text};
        border-radius: 8px; padding: 4px;
    }}
    QCheckBox {{ spacing: 8px; color: {p.text}; font-size: 13px; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 5px;
        border: 1px solid {p.border_strong}; background: {p.surface};
    }}
    QCheckBox::indicator:checked {{ background: {BRAND}; border-color: {BRAND};
        image: url(none); }}

    /* Scrollbars */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ height: 0; }}

    /* Status bar */
    QLabel#Status {{ color: {p.text_muted}; font-size: 12px; }}
    QProgressBar {{ border: none; background: {p.brand_tint}; border-radius: 3px; max-height: 6px; }}
    QProgressBar::chunk {{ background: {BRAND}; border-radius: 3px; }}

    /* Rows (queue / history) */
    QFrame#Row {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
    QFrame#Row:hover {{ border-color: {p.border_strong}; }}
    QFrame#ThumbTile {{ background: {p.surface}; border: 2px solid {p.border}; border-radius: 12px; }}
    QFrame#ThumbTile[selected="true"] {{ border-color: {BRAND}; }}
    """
