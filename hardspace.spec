# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Marian desktop client → single windowed .exe.
from PyInstaller.utils.hooks import collect_submodules, collect_all

# keyring loads its backend dynamically; mcp/pydantic have dynamic imports.
hiddenimports = [
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.macOS",
    "keyring.backends.chainer",
]
hiddenimports += collect_submodules("mcp")

datas, binaries = [], []
for pkg in ("mcp",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Marian",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed GUI (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
