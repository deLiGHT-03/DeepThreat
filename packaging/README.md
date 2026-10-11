# Windows packaging

Run `packaging\build_windows.ps1` in PowerShell on Windows. It creates a one-folder PyInstaller app in `dist\DeepThreat`. If Inno Setup 6 is installed, it also creates `release\DeepThreatSetup.exe`.

The installer uses a stable application ID and per-user install directory. Re-running it upgrades the existing installation and removes stale app files first; saved usernames and league selections remain in `%APPDATA%\DeepThreat`, outside the install directory.

The build includes only `templates` and `static` as app assets, plus the data files the optional `google-genai` package needs for Ask AI (see `DeepThreat.spec`). `api-key.txt` is deliberately not included in the executable or installer: an installed copy reads its key from `%APPDATA%\DeepThreat\api-key.txt`, which keeps one person's key out of a build that could be copied to another machine.

The build also embeds `DeepThreat.ico` in the executable, so Windows shows the app's own icon on
the executable, its shortcuts and the taskbar. That file is generated - after changing
`static/deepthreat-arc-logo.png`, rebuild it with Python (Pillow, see the repository's
`requirements-dev.txt`):

```
python packaging\make_icon.py
```

The icon is the arc logo on a dark rounded tile. The logo on its own is pale strokes on nothing,
which disappears against a light wallpaper and thins out to a few grey pixels at 16 px; the tile
gives it something to sit on at every size.
