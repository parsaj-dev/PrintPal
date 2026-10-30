"""Create the Qt application and run the main window."""
from __future__ import annotations

import sys

from printpal.config import Config


def run_app(config: Config, initial_path: str | None = None, log=None,
            start_hidden: bool = False) -> int:
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    from PySide6.QtGui import QIcon

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PrintPal")
    app.setOrganizationName("PrintPal")

    from printpal.qtui import theme
    from printpal.qtui.window import MainWindow, _asset_path
    # Theme the whole app before the first window paints (no flash of the
    # Windows colours, and dialogs match too).
    theme.apply(app, config.dark_mode)
    icon = _asset_path("icon.png")
    if icon:
        app.setWindowIcon(QIcon(icon))

    tray = QSystemTrayIcon.isSystemTrayAvailable()
    # With a tray icon the app outlives its window (closing hides to the tray);
    # MainWindow quits explicitly when that's what the user asked for.
    app.setQuitOnLastWindowClosed(not tray)

    win = MainWindow(config, initial_path=initial_path, log=log)
    if not (start_hidden and tray):
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
