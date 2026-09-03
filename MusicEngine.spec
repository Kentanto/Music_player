# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

project_dir = Path(SPECPATH)

hiddenimports = (
    collect_submodules("yt_dlp")
    + collect_submodules("spotipy")
    + ["PySide6.QtMultimedia"]
)

datas = [
    (str(project_dir / "ui" / "styles.qss"), "ui"),
]

if sys.platform == "win32":
    datas.append((str(project_dir / "ffmpeg" / "bin"), "ffmpeg/bin"))


analysis = Analysis(
    [str(project_dir / "main.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="MusicEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="MusicEngine",
)
