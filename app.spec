# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec for Release Build
# 
# This spec file builds a clean, standalone executable with:
# - Professional application metadata embedded
# - No UPX compression or executable packing
# - No debug symbols or code obfuscation
# - Full version and company information for antivirus trust
#
# Result: A legitimate-looking, transparent, antivirus-safe standalone .exe file
#
# Usage:
#   pyinstaller app.spec --clean --noconfirm

a = Analysis(
    ['src\\gui\\app.py'],
    pathex=[],
    binaries=[],
    # Include data files needed by the app (if any)
    datas=[
        ('data', 'data'),  # Include data directory with rules, etc.
    ],
    hiddenimports=[
        'src.mock_deploy',
        'src.mock_deploy.engine',
        'src.mock_deploy.mutation', 
        'src.mock_deploy.state'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test and mock dependencies
        'tests',
        'pytest',
        'black',
        'ruff',
    ],
    noarchive=False,
    # Optimize bytecode: 0=none, 2=strip debuginfo (safe for Release)
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='7d2d-mod-manager',
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
    # version='file_version_info.txt',  # Temporarily disabled
)
