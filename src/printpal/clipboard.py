"""Read a PDF file path from the Windows clipboard.

Tries, in order:
1. CF_HDROP (files copied in Explorer)
2. CF_UNICODETEXT / CF_TEXT containing a valid file path

On non-Windows (for testing), falls back to a stub that always returns None.
"""
from __future__ import annotations

import os
import sys


def _strip_quotes(s: str) -> str:
    """Windows 'Copy as path' wraps in double quotes."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _is_supported_file(path: str) -> bool:
    lower = path.lower()
    return os.path.isfile(path) and (lower.endswith(".pdf") or lower.endswith(".png") or lower.endswith(".jpg") or lower.endswith(".jpeg"))


if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    CF_HDROP = 15
    CF_UNICODETEXT = 13

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32

    shell32.DragQueryFileW.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT, ctypes.wintypes.LPWSTR, ctypes.wintypes.UINT]
    shell32.DragQueryFileW.restype = ctypes.wintypes.UINT

    def _get_hdrop_files() -> list[str]:
        """Get file paths from CF_HDROP clipboard data."""
        if not user32.OpenClipboard(None):
            return []
        try:
            h = user32.GetClipboardData(CF_HDROP)
            if not h:
                return []
            count = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
            paths = []
            for i in range(count):
                buf = ctypes.create_unicode_buffer(260)
                shell32.DragQueryFileW(h, i, buf, 260)
                paths.append(buf.value)
            return paths
        finally:
            user32.CloseClipboard()

    def _get_text() -> str | None:
        if not user32.OpenClipboard(None):
            return None
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()

    def get_pdf_path() -> str | None:
        """Return a supported file path from the clipboard, or None."""
        # try CF_HDROP first
        for p in _get_hdrop_files():
            if _is_supported_file(p):
                return p

        # try text
        text = _get_text()
        if text:
            path = _strip_quotes(text.strip().splitlines()[0])
            if _is_supported_file(path):
                return path

        return None

else:
    def get_pdf_path() -> str | None:
        """Stub for non-Windows. Returns None."""
        return None
