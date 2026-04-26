# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

tb_datas, tb_binaries, tb_hiddenimports = collect_all("ttkbootstrap")
pil_datas, pil_binaries, pil_hiddenimports = collect_all("PIL")

datas = tb_datas + pil_datas
binaries = tb_binaries + pil_binaries
hiddenimports = tb_hiddenimports + pil_hiddenimports

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BiliFavoritesClassifier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
