"""Small per-user settings store for the local desktop app."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# Several page requests are served at once, and every one of them saves the same
# settings. Writes are serialised here, and each one swaps in a temporary file of its
# own: two writers sharing one temporary file lose it to whoever replaces it first.
_write_lock = threading.Lock()
_REPLACE_ATTEMPTS = 5
_REPLACE_PAUSE = 0.02


def _settings_path() -> Path:
    app_data = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    return root / "DeepThreat" / "settings.json"


def load_connection() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _replace(temporary_path: Path, settings_path: Path) -> None:
    """Move the temporary file into place, waiting out another writer that holds it."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            temporary_path.replace(settings_path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_PAUSE)


def save_connection(username: str, manager_id: str = "", league_id: str = "",
                    history_league_id: str = "") -> None:
    settings_path = _settings_path()
    with _write_lock:
        data = load_connection()
        if username and data.get("username", "").casefold() != username.casefold():
            data = {}
        if username:
            data["username"] = username
        if manager_id:
            data["manager_id"] = str(manager_id)
        if league_id:
            data["league_id"] = str(league_id)
        if history_league_id:
            data["history_league_id"] = str(history_league_id)
        temporary_path = settings_path.with_name(
            f"{settings_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            _replace(temporary_path, settings_path)
        except OSError:
            # Two ways this is not an error. Another process may be swapping the same file in --
            # the settings are only a remembered default and every writer saves the same values,
            # so a lost race must never turn into a failed page; the next request writes again.
            # Or there is nowhere to write at all: a hosted copy runs where the filesystem can
            # be read-only, and remembering the connection is a convenience, not the answer.
            pass
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def clear_connection() -> None:
    try:
        _settings_path().unlink()
    except OSError:
        # Already gone, or a filesystem that cannot be written to at all -- either way there is
        # nothing remembered for the app to forget.
        pass
