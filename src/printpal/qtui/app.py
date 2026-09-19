"""Create the Qt application and run the main window."""
from __future__ import annotations

import os
import sys

from printpal.config import Config


def run_app(config: Config, initial_path: str | None = None, log=None) -> int:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon

    # Crisp on high-DPI Windows displays.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PrintPal")
    app.setOrganizationName("PrintPal")

    from printpal.qtui.window import MainWindow, _asset_path
    icon = _asset_path("icon.png")
    if icon:
        app.setWindowIcon(QIcon(icon))

    win = MainWindow(config, initial_path=initial_path, log=log)
    win.show()
    return app.exec()


def show_error(title: str, message: str) -> None:
    """Standalone error dialog (used when the main window can't come up)."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, title, message)
    except Exception:
        sys.stderr.write(f"{title}: {message}\n")
