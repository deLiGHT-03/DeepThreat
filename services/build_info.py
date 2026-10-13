"""Which revision this process is running, read so a health check can report it.

A deployed copy and the checkout it was built from look the same from the outside: both answer
the same pages, and a host that has not released the newest commit is indistinguishable from one
that has, because the only sign is a number that looks like the number it should be. The one
thing a running copy can say about itself that a page cannot is the commit it was built from, and
this reads it from wherever the host recorded it, in the order the host is most likely to have
said it:

* a revision the host sets in the environment -- ``RENDER_GIT_COMMIT`` on Render,
  ``SOURCE_VERSION`` on Heroku and the platforms that copied its convention -- or
  ``DEEPTHREAT_COMMIT`` for a host that sets none of its own, such as a container given the SHA
  when it is run;
* failing that, the checkout this file sits in, which is what a local run is. That one is marked
  ``dirty`` when tracked files differ from the commit, because a sha that names a commit the
  working tree has moved on from would otherwise read as a revision nothing ever shipped.

Nothing is guessed. A copy that is told none of this answers ``null`` and says so in ``source``,
since a health check that invents a revision is worse than one that admits it does not know. The
answer is read once and kept: it cannot change while the process lives, and the platform asks
often.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

#: The checkout this file belongs to, which is what a run from source is served out of.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Revision variables a host sets, most specific first. The name is the variable, the value is
#: what the report says it learned the revision from.
_REVISION_VARIABLES = (
    ("DEEPTHREAT_COMMIT", "environment"),
    ("RENDER_GIT_COMMIT", "render"),
    ("SOURCE_VERSION", "host"),
    ("GIT_COMMIT", "host"),
    ("COMMIT_SHA", "host"),
)

#: The same for the branch, where the host names one too.
_BRANCH_VARIABLES = ("DEEPTHREAT_BRANCH", "RENDER_GIT_BRANCH", "GIT_BRANCH", "SOURCE_BRANCH")

#: A full Git object name. A longer value is not one, so it is cut to this rather than reported.
_SHA_LENGTH = 40

#: ``git`` answers a checkout immediately, so one that does not is a broken one: the report says
#: nothing rather than holding a health check open behind it.
_GIT_TIMEOUT = 5


def _text(value: str | None) -> str:
    return (value or "").strip()


def _revision_from_environment() -> tuple[str, str] | None:
    """The revision the host says it built, and which variable it came from."""
    for variable, source in _REVISION_VARIABLES:
        value = _text(os.environ.get(variable))
        if value:
            return value[:_SHA_LENGTH], source
    return None


def _branch_from_environment() -> str | None:
    for variable in _BRANCH_VARIABLES:
        value = _text(os.environ.get(variable))
        if value:
            return value
    return None


def _git(checkout: Path, *arguments: str) -> str | None:
    """One read-only git command in the checkout, or None when git cannot answer it."""
    try:
        finished = subprocess.run(
            ("git", "-C", str(checkout), *arguments),
            capture_output=True, text=True, errors="replace", timeout=_GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # No git on this machine, or one that did not answer in time. Neither is a reason to fail
        # the request the platform is asking about, so the report simply does not know.
        return None
    if finished.returncode != 0:
        return None
    return finished.stdout.strip()


@lru_cache(maxsize=None)
def describe(checkout: Path | None = None) -> dict[str, object]:
    """What this copy can say about the revision it is running.

    Keys: ``commit`` (or None), ``branch`` (or None), ``source`` -- which of ``environment``,
    ``render``, ``host``, ``checkout`` or ``unknown`` answered -- and, for a checkout,
    ``dirty``: whether tracked files differ from that commit.

    ``checkout`` names the tree to read when nothing in the environment does, and defaults to
    this file's own project. Cached, because it cannot change while the process lives.
    """
    root = PROJECT_ROOT if checkout is None else Path(checkout)
    report: dict[str, object] = {"commit": None, "branch": None, "source": "unknown"}

    from_host = _revision_from_environment()
    if from_host is not None:
        report["commit"], report["source"] = from_host
        report["branch"] = _branch_from_environment()
        return report

    commit = _git(root, "rev-parse", "HEAD")
    if not commit:
        # Not a checkout, and nothing in the environment: the answer is that this copy does not
        # know, which is a fact about the deploy rather than a failure to report one.
        return report
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    changes = _git(root, "status", "--porcelain", "--untracked-files=no")
    report.update({
        "commit": commit[:_SHA_LENGTH],
        # A detached checkout answers "HEAD", which names no branch and is not reported as one.
        "branch": None if branch in (None, "", "HEAD") else branch,
        "source": "checkout",
        # Untracked files are not asked about: a file git was never told about (a scratch dump, a
        # local notes file) is not the working tree having moved on from the commit.
        "dirty": bool(changes),
    })
    return report
