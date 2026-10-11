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

# Ask AI imports google-genai, and that package ships data files of its own, so its modules and
# its data are named here rather than left to be discovered. A machine without the package still
# builds: every other page runs without it, and only the Ask AI tab needs it.
try:
    from PyInstaller.utils.hooks import collect_data_files, collect_submodules

    datas += collect_data_files("google.genai")
    hiddenimports = collect_submodules("google.genai")
except Exception:  # A build without google-genai is still a build.
    hiddenimports = []

a = Analysis(
    [str(project_dir / "run.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
