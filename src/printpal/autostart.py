"""Start PrintPal hidden in the tray when the user signs in to Windows.

A warm, already-running PrintPal is what makes "Print to PrintPal" feel instant:
the job goes straight into its spool instead of cold-starting the app (seconds
on an old PC). This toggles the per-user ``Run`` registry value the installer
also sets -- no admin rights, and nothing to clean up beyond that one value.
"""
from __future__ import annotations

import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "PrintPal"


def available() -> bool:
    """Only for the installed/portable build: a dev checkout has no exe to run."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def command() -> str:
    return f'"{sys.executable}" --background'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, _VALUE)
    except OSError:
        return False
    return bool(value)


def set_enabled(on: bool) -> None:
    if not available():
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, _VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE)
            except OSError:
                pass
