# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — EDM Analyzer (Techbak Solutions)
# Uso: pyinstaller edm_search.spec

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Assets do DearPyGui (fontes e recursos internos)
dpg_datas = collect_data_files('dearpygui')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=collect_dynamic_libs('dearpygui'),
    datas=[
        ('model.pkl',      '.'),          # modelo ML treinado
        ('config.py',      '.'),          # taxonomia de gêneros
        ('.env.example',   '.'),          # referência de variáveis de ambiente
        *dpg_datas,
    ],
    hiddenimports=[
        'sklearn.ensemble._forest',
        'sklearn.tree._classes',
        'sklearn.preprocessing._data',
        'sklearn.utils._bunch',
        'soundfile',
        'librosa',
        'numpy',
        'dearpygui.dearpygui',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'tkinter', 'PyQt5', 'PyQt6'],
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
    name='EDM Analyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,           # True = CLI funciona; False = sem janela de console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='file_version_info.txt',
    # icon='assets/icon.ico',  # descomente quando tiver o ícone
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EDM Analyzer',
)
