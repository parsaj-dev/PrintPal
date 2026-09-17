"""Read a PDF file path from the Windows clipboard.

Tries, in order:
1. CF_HDROP (files copied in Explorer)
2. CF_UNICODETEXT containing a file:/// URL, a quoted path, or a plain path

Uses win32clipboard from pywin32 on Windows for reliable clipboard access.
On non-Windows (for testing), falls back to a stub that always returns None.
"""
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import unquote, urlparse

log = logging.getLogger("printpal")

_SUPPORTED_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")


def _strip_quotes(s: str) -> str:
    """Windows 'Copy as path' wraps in double quotes."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _file_url_to_path(url: str) -> str | None:
    """Convert a file:/// URL to a local path, or return None if not a file URL."""
    url = url.strip()
    if not url.lower().startswith("file:"):
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() != "file":
        return None
    path = unquote(parsed.path)
    # on Windows, file:///C:/foo comes through as /C:/foo -- strip the leading slash
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    # normalize forward slashes to backslashes for Windows
    path = path.replace("/", os.sep)
    return path


def _is_supported_file(path: str) -> bool:
    exists = os.path.isfile(path)
    supported = path.lower().endswith(_SUPPORTED_EXT)
    log.debug("_is_supported_file(%r): exists=%s, supported_ext=%s", path, exists, supported)
    return exists and supported


if sys.platform == "win32":
    import win32clipboard
    import win32con

    def _get_hdrop_files() -> list[str]:
        """Get file paths from CF_HDROP clipboard data."""
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                    filenames = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                    log.debug("CF_HDROP files: %s", filenames)
                    return list(filenames)
                else:
                    log.debug("CF_HDROP not available")
                    return []
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:
            log.warning("Failed to read CF_HDROP: %s", e)
            return []

    def _get_text() -> str | None:
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    log.debug("Clipboard text (first 200 chars): %r", text[:200] if text else None)
                    return text
                else:
                    log.debug("CF_UNICODETEXT not available")
                    return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:
            log.warning("Failed to read clipboard text: %s", e)
            return None

    def get_pdf_path() -> str | None:
        """Return a supported file path from the clipboard, or None."""
        log.info("Reading clipboard...")

        # try CF_HDROP first (file copied in Explorer)
        for p in _get_hdrop_files():
            if _is_supported_file(p):
                log.info("Found via CF_HDROP: %s", p)
                return p

        # try text (file:/// URL, quoted path, or plain path)
        text = _get_text()
        if text:
            line = _strip_quotes(text.strip().splitlines()[0])
            log.debug("Clipboard text after strip: %r", line)

            # file:/// URL from a browser address bar
            converted = _file_url_to_path(line)
            if converted:
                log.debug("Converted file URL to path: %r", converted)
                if _is_supported_file(converted):
                    log.info("Found via file:// URL: %s", converted)
                    return converted

            # plain path (e.g. "Copy as path" from Explorer)
            if _is_supported_file(line):
                log.info("Found via text path: %s", line)
                return line

        log.info("No supported file found on clipboard.")
        return None

else:
    def get_pdf_path() -> str | None:
        """Stub for non-Windows. Returns None."""
        return None
