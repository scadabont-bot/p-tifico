# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

pillow_datas, pillow_binaries, pillow_hiddenimports = collect_all("PIL")

a = Analysis(
    ["SCADA_BONT_V50_FIELD_MAP (2) - RR FIXNEW01.py"],
    pathex=[],
    binaries=pillow_binaries,
    datas=pillow_datas,
    hiddenimports=pillow_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="p-tifico-backup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["SCADA_BONT.ico"],
    version="installer/p-tifico-backup-version.txt",
)
