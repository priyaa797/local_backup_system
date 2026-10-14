# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Local Backup System Windows executable build."""

import sys
from pathlib import Path

block_cipher = None
project_root = Path.cwd()

added_files = [
    (str(project_root / 'assets' / 'icon.png'), 'assets'),
    (str(project_root / 'assets' / 'icon.ico'), 'assets'),
]

a = Analysis(
    ['src/main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        'keyring.backends.SecretService',
        'cryptography',
        'cryptography.fernet',
        'paramiko',
        'psutil',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
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
    name='LocalBackupSystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Windowed GUI application without terminal popup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / 'assets' / 'icon.ico') if (project_root / 'assets' / 'icon.ico').exists() else None,
)
