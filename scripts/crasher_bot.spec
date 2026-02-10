# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Crasher Bot."""

import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / 'crasher_bot' / 'gui.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[('../bot_config.json', '.')],
    hiddenimports=[
        'crasher_bot',
        'crasher_bot.config',
        'crasher_bot.cli',
        'crasher_bot.gui',
        'crasher_bot.core',
        'crasher_bot.core.driver',
        'crasher_bot.core.engine',
        'crasher_bot.core.hotstreak',
        'crasher_bot.core.session',
        'crasher_bot.strategies',
        'crasher_bot.ui',
        'crasher_bot.ui.app',
        'crasher_bot.ui.widgets',
        'numpy',
        'selenium',
        'undetected_chromedriver',
    ],
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

if sys.platform == 'darwin':
    # macOS: build a .app bundle
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='CrashOut',
        icon=str(ROOT / 'assets' / 'icon.icns'),
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='CrashOut',
    )
    app = BUNDLE(
        coll,
        bundle_identifier='com.crashout.app',
        name='CrashOut.app',
        icon=str(ROOT / 'assets' / 'icon.icns'),
        info_plist={
            'CFBundleShortVersionString': '2.0.0',
            'CFBundleName': 'CrashOut',
            'NSHighResolutionCapable': True,
        },
    )
else:
    # Windows / Linux: single-file executable
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='CrashOut',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
    )
