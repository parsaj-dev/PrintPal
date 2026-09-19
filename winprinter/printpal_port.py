"""Single entry point for the winprinter component -> PrintPalPort.exe.

Subcommands:
    catch   read a spooled print job (stdin or --file) and hand it to PrintPal
            (used by a redirection port monitor)
    watch   watch the printer's incoming folder and hand off finished jobs
            (used by the file-port install method; runs at login)

Keeping both behind one console exe keeps packaging and the install scripts
simple. See winprinter/README.md.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printpal_catcher  # noqa: E402
import printpal_watcher  # noqa: E402

_USAGE = "usage: PrintPalPort.exe {catch|watch} [options]\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write(_USAGE)
        return 64
    cmd, rest = argv[0], argv[1:]
    if cmd == "catch":
        return printpal_catcher.main(rest)
    if cmd == "watch":
        return printpal_watcher.main(rest)
    sys.stderr.write(_USAGE)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
