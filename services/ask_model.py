"""The Gemini key and model behind Ask AI, and the one place that decides if it is usable.

The key is looked for in the environment first and then in a file, because the two places this
app runs want different things: a source checkout can export `GEMINI_API_KEY` for a session,
while an installed copy needs somewhere durable to keep the key, and the install directory is
not hers to write to. The file is `api-key.txt` beside the app when run from source, or in the
per-user settings folder (`%APPDATA%\\DeepThreat`) for an installed copy, and it stays out of
the build and out of version control.

The key is read here rather than in the page code so that every caller -- the page, the route
and the error message that says a key is missing -- asks the same question and gets the same
answer. The value itself is never logged, echoed or returned to a template; callers ask whether
a model is reachable, not what the key is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: The model Ask AI asks. Flash is the fast tier, which suits questions that are mostly a few
#: lookups followed by one short answer, and one question costs several calls: a tier that takes
#: a minute to answer a sentence makes a question that needs three lookups a four-minute wait.
MODEL = "gemini-3.5-flash"

#: The models tried, in order, when the one before them cannot answer.
#:
#: A tier can be briefly out of capacity -- the API's own words are "this model is currently
#: experiencing high demand" -- and that is a reason to ask a different model, not to fail the
#: question. The fallback is a lite tier, which is cheaper and, measured here, quicker; it
#: answers a little more plainly than the model above it, which is the right trade for an answer
#: that would otherwise not arrive.
FALLBACK_MODELS = ("gemini-3.1-flash-lite",)

#: Every model Ask AI may ask, in the order it asks them.
MODEL_CHAIN = (MODEL,) + FALLBACK_MODELS

#: How long one call may take before that model is treated as unreachable and the next is
#: asked. A call that hangs is worse than one that fails: the page waits, and nothing says why.
#: Measured against this API, a healthy call is a second or two and a struggling tier is 20s or
#: more, so this is long enough for the model to think and short enough to give up on it.
CALL_TIMEOUT_MS = 25_000

#: The file a key can be kept in, in the app folder or the per-user settings folder.
KEY_FILENAME = "api-key.txt"

#: Environment variables the SDK itself understands, checked in this order.
ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


class AiUnavailable(Exception):
    """Ask AI cannot run: no key was found, or the SDK is not installed."""


def key_paths() -> list[Path]:
    """Where a key file is looked for, in the order it wins.

    A frozen build unpacks its code to a temporary folder, so the file that ships beside the
    source is not the one to write to; the settings folder is listed last and is the one an
    installed copy can actually be given a key in.
    """
    app_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    paths = [app_dir / KEY_FILENAME]
    app_data = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if app_data:
        paths.append(Path(app_data) / "DeepThreat" / KEY_FILENAME)
    return paths


def find_key() -> tuple[str, str]:
    """Return the key and where it came from, or two empty strings when there is none.

    The description is a variable name or a path, which is safe to show: it says where the key
    was found without saying what it is.
    """
    for variable in ENV_VARS:
        value = (os.environ.get(variable) or "").strip()
        if value:
            return value, variable
    for path in key_paths():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value, str(path)
    return "", ""


def available() -> bool:
    """Whether a key was found. The SDK is imported separately, so this stays cheap."""
    return bool(find_key()[0])


def key_source() -> str:
    """Where the key came from, for a settings line that names the place but not the key."""
    return find_key()[1]


def client():
    """Build a Gemini client, or raise :class:`AiUnavailable` with something to show a reader.

    The SDK is imported here rather than at module import so that the app starts and every
    other page renders whether or not it is installed.
    """
    key, _ = find_key()
    if not key:
        raise AiUnavailable(
            "Ask AI needs a Gemini API key. Put your key in api-key.txt beside the app "
            "(or in %APPDATA%\\DeepThreat\\api-key.txt), or set GEMINI_API_KEY."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - depends on the installed environment.
        raise AiUnavailable(
            "Ask AI needs the google-genai package. Run: python -m pip install -r requirements.txt"
        ) from exc
    # One client, one timeout, built here so every caller gets the same bound on a call that
    # would otherwise wait on the API as long as the API feels like taking.
    return genai.Client(api_key=key,
                        http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS))
