# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import shutil
import sys
from PyInstaller.utils.hooks import collect_submodules

project_dir = Path(globals().get("SPECPATH", Path.cwd())).resolve()

hiddenimports = (
    collect_submodules("yt_dlp")
    + collect_submodules("spotipy")
    + ["PySide6.QtMultimedia"]
)

datas = [
    (str(project_dir / "ui" / "styles.qss"), "ui"),
]

ffmpeg_dir = project_dir / "ffmpeg" / "bin"

if sys.platform == "win32" and ffmpeg_dir.exists():
    datas.append((str(ffmpeg_dir), "ffmpeg/bin"))

use_upx = shutil.which("upx") is not None

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
    [],
    name="MusicEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=use_upx,
    console=True,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=use_upx,
    name="MusicEngine",
)
