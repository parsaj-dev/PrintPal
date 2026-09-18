# PyInstaller spec for PrintPal
#
# Build with: pyinstaller printpal.spec
# Output: dist/PrintPal/ (one-folder mode for fast startup)

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
    for name in ("libzbar-0.dll", "libiconv-2.dll"):
        candidate = pyzbar_dir / name
        if candidate.exists():
            zbar_dll.append((str(candidate), "."))
except ImportError:
    pass

# The virtual-printer install scripts ship alongside the exe so the user can run
# them from the install folder (see winprinter/README.md).
_winprinter_datas = [
    ("winprinter/install_printer.ps1", "."),
    ("winprinter/uninstall_printer.ps1", "."),
    ("winprinter/README.md", "winprinter"),
]

# Trim Qt modules PrintPal never uses -- keeps the PySide6 bundle small.
_qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngine",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "tkinter", "_tkinter",
]

a = Analysis(
    ["src/printpal/main.py"],
    pathex=["src"],
    binaries=zbar_dll,
    datas=[("assets/icon.png", "assets")] + _winprinter_datas,
    hiddenimports=["printpal", "printpal.qtui.app", "printpal.qtui.window"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_qt_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PrintPal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

# PrintPalPort.exe -- the virtual-printer catcher/watcher. A console exe (a
# redirection port monitor pipes the job to its stdin).
port = Analysis(
    ["winprinter/printpal_port.py"],
    pathex=["winprinter"],
    binaries=[],
    datas=[],
    hiddenimports=["printpal_catcher", "printpal_watcher", "jobio"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "tkinter", "_tkinter", "numpy", "cv2", "PIL", "pymupdf"],
    cipher=block_cipher,
    noarchive=False,
)
port_pyz = PYZ(port.pure, port.zipped_data, cipher=block_cipher)
port_exe = EXE(
    port_pyz,
    port.scripts,
    [],
    exclude_binaries=True,
    name="PrintPalPort",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    port_exe,
    port.binaries,
    port.zipfiles,
    port.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PrintPal",
)
