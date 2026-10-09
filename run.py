"""Start the local fantasy football web app and open it in Firefox."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from app import create_app


HOST = "127.0.0.1"
PORT = int(os.environ.get("FANTASY_APP_PORT", "0"))


def _open_browser(url: str) -> None:
    """Prefer Firefox; use a resizable Edge app window when Firefox is unavailable."""
    firefox = shutil.which("firefox") or shutil.which("firefox.exe")
    if not firefox and os.name == "nt":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Mozilla Firefox" / "firefox.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Mozilla Firefox" / "firefox.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Mozilla Firefox" / "firefox.exe",
        ]
        firefox = next((str(path) for path in candidates if path.is_file()), None)
    if firefox:
        subprocess.Popen([firefox, "-new-window", url])
        return

    edge = shutil.which("msedge") or shutil.which("msedge.exe")
    if not edge and os.name == "nt":
        edge_candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
        edge = next((str(path) for path in edge_candidates if path.is_file()), None)
    if edge:
        subprocess.Popen([edge, f"--app={url}"])
        return
    webbrowser.open(url, new=1)


def open_browser(url: str) -> None:
    """Wait for Flask to start, then open a resizeable local app window."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    _open_browser(url)


def _available_port() -> int:
    configured_port = int(os.environ.get("FANTASY_APP_PORT", "0"))
    if configured_port:
        return configured_port
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and sys.stderr is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stdout
    PORT = _available_port()
    url = f"http://{HOST}:{PORT}"
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    create_app().run(host=HOST, port=PORT, debug=False)
