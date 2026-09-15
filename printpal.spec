# PyInstaller spec for PrintPal
#
# Build with: pyinstaller printpal.spec
# Output: dist/PrintPal.exe
#
# This bundles the zbar DLL that pyzbar needs on Windows. If the build
# fails to find it, make sure zbar is installed or the DLL is in PATH.

import os
import sys
from pathlib import Path

block_cipher = None

# Find the zbar DLL bundled with pyzbar
zbar_dll = []
try:
    import pyzbar
    pyzbar_dir = Path(pyzbar.__file__).parent
    for dll in pyzbar_dir.glob("*.dll"):
        zbar_dll.append((str(dll), "."))
    # also check for libiconv and libzbar in common locations
    for name in ("libzbar-0.dll", "libiconv-2.dll"):
        candidate = pyzbar_dir / name
        if candidate.exists():
            zbar_dll.append((str(candidate), "."))
except ImportError:
    pass

a = Analysis(
    ["src/printpal/main.py"],
    pathex=["src"],
    binaries=zbar_dll,
    datas=[],
    hiddenimports=["printpal"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PrintPal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="icon.ico",  # uncomment when icon is ready
)
