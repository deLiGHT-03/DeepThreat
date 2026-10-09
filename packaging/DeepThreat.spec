# PyInstaller one-folder build: fast startup, with only app assets included.
from pathlib import Path

project_dir = Path(SPECPATH).resolve().parent
# The app's icon, drawn by packaging/make_icon.py: without this the executable carries
# PyInstaller's default, so every shortcut Windows creates from it shows that instead.
app_icon = Path(SPECPATH) / "DeepThreat.ico"
datas = [
    (str(project_dir / "templates"), "templates"),
    (str(project_dir / "static"), "static"),
]

a = Analysis(
    [str(project_dir / "run.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest", "IPython", "jupyter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepThreat",
    icon=str(app_icon),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="DeepThreat",
)
