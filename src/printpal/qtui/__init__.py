"""PrintPal's modern desktop UI, built on PySide6/Qt.

Replaces the old tkinter/ttk window with a genuinely modern interface: proper
spacing and type, light/dark themes, the orange/brown brand, and the batch
queue, history + reprint, smart-routing and N-up features surfaced properly.
"""
from printpal.qtui.app import run_app

__all__ = ["run_app"]
