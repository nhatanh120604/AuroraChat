# ChatClient.spec
from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ
from PyInstaller.building.api import EXE, COLLECT
from PyInstaller.building.datastruct import Tree
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None
ROOT = Path.cwd()

datas = [
    (str(ROOT / 'client' / '.env'), '.'),
]
datas += collect_data_files('cryptography')

hiddenimports = (
    collect_submodules('socketio')
    + collect_submodules('cryptography')
    + ['eventlet']
)

binaries = collect_dynamic_libs('cryptography.hazmat.bindings')

a = Analysis(
    ['client/client.py'],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
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
    [],
    exclude_binaries=True,
    name='ChatClient',
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
    Tree(str(ROOT / 'client' / 'qml'), prefix='qml'),
    Tree(str(ROOT / 'client' / 'assets'), prefix='assets'),
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ChatClient',
)
