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


def apply(app, dark: bool) -> None:
    """Theme the whole application: every window, dialog and message box.

    PrintPal draws its own light/dark look, so it must not inherit the Windows
    colour scheme: on a PC set to dark mode Qt would otherwise hand dialogs a
    dark background under our dark-on-light text (the unreadable Settings
    window). Fusion + an explicit palette + an app-wide stylesheet makes every
    surface follow PrintPal's own theme. Fusion is also the lightest built-in
    style, which suits an old PC.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyleFactory
    if not app.property("printpal_styled"):
        style = QStyleFactory.create("Fusion")
        if style is not None:
            app.setStyle(style)
        app.setProperty("printpal_styled", True)
    try:  # Qt 6.8+: keep the OS light/dark setting from overriding ours.
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    except Exception:
        pass
    app.setPalette(qpalette(dark))
    app.setStyleSheet(stylesheet(dark, _glyphs(dark)))


def _glyphs(dark: bool) -> dict[str, str]:
    """Draw the few stylesheet glyphs (arrows, check mark) as small PNGs in the
    theme's colours. A styled combo/spin box/check box loses Qt's own arrows and
    tick, and bundling image files for three shapes isn't worth the packaging."""
    import os
    import tempfile
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    from printpal import __version__

    p = palette(dark)
    # Versioned so a later palette change never picks up stale glyphs.
    folder = os.path.join(tempfile.gettempdir(), f"printpal-ui-{__version__}")
    os.makedirs(folder, exist_ok=True)
    shapes = {
        "down": ([(8, 12), (16, 20), (24, 12)], p.text_muted),
        "up": ([(8, 20), (16, 12), (24, 20)], p.text_muted),
        "check": ([(8, 17), (14, 23), (25, 10)], "#FFFFFF"),
    }
    out = {}
    for name, (pts, colour) in shapes.items():
        path = os.path.join(folder, f"{name}-{'dark' if dark else 'light'}.png")
        if not os.path.isfile(path):
            img = QImage(32, 32, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            pnt = QPainter(img)
            pnt.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(colour), 3.2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            pnt.setPen(pen)
            pnt.drawPolyline([QPointF(x, y) for x, y in pts])
            pnt.end()
            img.save(path, "PNG")
        out[name] = path.replace("\\", "/")
    return out


def qpalette(dark: bool):
    """A QPalette matching the theme, for anything the stylesheet doesn't reach
    (native-drawn bits, disabled states, selection, tooltips)."""
    from PySide6.QtGui import QColor, QPalette
    p = palette(dark)
    pal = QPalette()
    roles = {
        QPalette.Window: p.bg, QPalette.WindowText: p.text,
        QPalette.Base: p.surface, QPalette.AlternateBase: p.surface_alt,
        QPalette.Text: p.text, QPalette.Button: p.surface, QPalette.ButtonText: p.text,
        QPalette.ToolTipBase: p.surface, QPalette.ToolTipText: p.text,
        QPalette.PlaceholderText: p.text_muted, QPalette.BrightText: "#FFFFFF",
        QPalette.Highlight: BRAND, QPalette.HighlightedText: "#FFFFFF",
        QPalette.Link: BRAND, QPalette.Mid: p.border, QPalette.Dark: p.border_strong,
        QPalette.Light: p.surface, QPalette.Midlight: p.surface_alt,
    }
    for role, colour in roles.items():
        pal.setColor(role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(p.text_muted))
    return pal


def stylesheet(dark: bool, glyphs: dict[str, str] | None = None) -> str:
    p = palette(dark)
    g = glyphs or {}
    arrows = ""
    if g:
        arrows = f"""
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url("{g['down']}"); width: 12px; height: 12px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        border: none; background: transparent; width: 20px; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url("{g['up']}"); width: 10px; height: 10px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url("{g['down']}"); width: 10px; height: 10px; }}
    QCheckBox::indicator:checked {{ image: url("{g['check']}"); }}
    """
    return f"""
    * {{
        font-family: {FONT_STACK};
        color: {p.text};
        outline: 0;
    }}
    QWidget {{ font-size: 14px; }}
    QWidget#Root, QDialog, QMessageBox {{ background: {p.bg}; }}
    QMessageBox QLabel {{ font-size: 13px; }}
    QToolTip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
        padding: 4px 8px; border-radius: 6px; }}
    QMenu {{ background: {p.surface}; border: 1px solid {p.border_strong}; padding: 4px;
        border-radius: 8px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p.brand_tint}; }}

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
    QLabel#Section {{ font-size: 12px; font-weight: 700; color: {p.text_muted};
        padding-top: 6px; }}
    QLabel#Muted {{ color: {p.text_muted}; font-size: 13px; }}
    QLabel#Tiny {{ color: {p.text_muted}; font-size: 11px; }}
    QLabel#Warn {{ color: {p.amber}; font-size: 12px; }}

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
    QPushButton#Danger {{ background: transparent; border: 1px solid {p.border_strong}; }}
    QPushButton#Danger:hover {{ background: {p.red_tint}; border-color: {p.red}; color: {p.red}; }}
    QPushButton#IconBtn {{ background: transparent; border: none; padding: 6px 10px;
        border-radius: 9px; color: {p.text_muted}; font-size: 15px; }}
    QPushButton#IconBtn:hover {{ background: {p.surface_alt}; color: {p.text}; }}

    /* Inputs */
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        background: {p.surface}; border: 1px solid {p.border_strong};
        border-radius: 9px; padding: 6px 10px; min-height: 20px; color: {p.text};
        selection-background-color: {BRAND}; selection-color: #FFFFFF;
    }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {BRAND}; }}
    QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {p.text_muted};
        background: {p.surface_alt}; }}
    QComboBox QAbstractItemView {{
        background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong};
        selection-background-color: {p.brand_tint}; selection-color: {p.text};
        padding: 4px;
    }}
    QCheckBox {{ spacing: 8px; color: {p.text}; font-size: 13px; }}
    QCheckBox:disabled {{ color: {p.text_muted}; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 5px;
        border: 1px solid {p.border_strong}; background: {p.surface};
    }}
    QCheckBox::indicator:hover {{ border-color: {BRAND}; }}
    QCheckBox::indicator:checked {{ background: {BRAND}; border-color: {BRAND}; }}

    /* Scroll areas: let the page background show through. */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
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
    """ + arrows
