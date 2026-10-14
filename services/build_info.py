"""Which revision this process is running, read so a health check can report it.

A deployed copy and the checkout it was built from look the same from the outside: both answer
the same pages, and a host that has not released the newest commit is indistinguishable from one
that has, because the only sign is a number that looks like the number it should be. The one
thing a running copy can say about itself that a page cannot is the commit it was built from, and
this reads it from wherever the host recorded it, in the order the host is most likely to have
said it:

* a revision the host sets in the environment -- ``RENDER_GIT_COMMIT`` on Render,
  ``VERCEL_GIT_COMMIT_SHA`` on Vercel, ``SOURCE_VERSION`` on Heroku and the platforms that copied
  its convention -- or ``DEEPTHREAT_COMMIT`` for a host that sets none of its own, such as a
  container given the SHA when it is run;
* failing that, the checkout this file sits in, which is what a local run is. That one is marked
  ``dirty`` when tracked files differ from the commit, because a sha that names a commit the
  working tree has moved on from would otherwise read as a revision nothing ever shipped;
* failing that, a revision a build wrote down beside this module -- ``tools/stamp_revision.py``,
  run while the host was building from its checkout, which is a place a running copy often has
  and the same copy has nothing else from. A stamp is read after the checkout and not before it:
  a tree that git can answer about is the tree itself, dirty flag and all, and a stamp is what a
  build recorded when there was no tree left to read.

Nothing is guessed. A copy that is told none of this answers ``null`` and says so in ``source``,
since a health check that invents a revision is worse than one that admits it does not know. A
host that has the variable and has not been told to hand it over reads the same as one that never
set one -- a Vercel project has to enable its system variables before any of them arrive -- and
that is the right answer for the same reason: neither is worth guessing at.

The answer is read once and kept: it cannot change while the process lives, and the platform asks
often.
"""

from __future__ import annotations

import os
import re
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
    ("VERCEL_GIT_COMMIT_SHA", "vercel"),
    ("SOURCE_VERSION", "host"),
    ("GIT_COMMIT", "host"),
    ("COMMIT_SHA", "host"),
)

#: The same for the branch, where the host names one too.
_BRANCH_VARIABLES = ("DEEPTHREAT_BRANCH", "RENDER_GIT_BRANCH", "VERCEL_GIT_COMMIT_REF",
                     "GIT_BRANCH", "SOURCE_BRANCH")

#: The stamp a build wrote down, beside this module. Read from the deployment, never from the
#: repository: it names one build, so a committed one would go stale at the next commit.
STAMP_PATH = Path(__file__).with_name("revision.txt")

#: A full Git object name. A longer value is not one, so it is cut to this rather than reported.
_SHA_LENGTH = 40

#: What a revision looks like, at its shortest and longest. Only a stamp is checked against this,
#: because only a stamp can say anything: a platform's variable is a platform's word, while a file
#: a build wrote is a file that can be a truncated variable or a template nobody filled in, and
#: reporting one of those as a revision would be worse than reporting none.
_SHA_SHAPE = re.compile(r"[0-9a-fA-F]{7,40}")

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


def _revision_from_stamp() -> str | None:
    """The revision a build wrote down for this deployment, if one did and it names a commit."""
    try:
        written = STAMP_PATH.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return written if _SHA_SHAPE.fullmatch(written) else None


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
    ``render``, ``vercel``, ``host``, ``checkout``, ``build`` or ``unknown`` answered -- ``stamp``
    (or None): the revision a build wrote down, whether or not it was the one reported, which is
    what says whether a build hook's file reached this copy at all -- and, for a checkout,
    ``dirty``: whether tracked files differ from that commit.

    ``checkout`` names the tree to read when nothing in the environment does, and defaults to
    this file's own project. Cached, because it cannot change while the process lives.
    """
    root = PROJECT_ROOT if checkout is None else Path(checkout)
    # The stamp is read whatever the answer turns out to be, so a report says whether a build wrote
    # one down even when something else named the revision: that is the only way to tell a build
    # hook that shipped its file from one the host left behind, and the file travels only if the
    # host packaged it.
    report: dict[str, object] = {"commit": None, "branch": None, "source": "unknown",
                                "stamp": _revision_from_stamp()}

    from_host = _revision_from_environment()
    if from_host is not None:
        report["commit"], report["source"] = from_host
        report["branch"] = _branch_from_environment()
        return report

    commit = _git(root, "rev-parse", "HEAD")
    if not commit:
        # Not a checkout, and nothing in the environment: a build may still have written the
        # revision down. Failing that, the answer is that this copy does not know, which is a
        # fact about the deploy rather than a failure to report one.
        if report["stamp"]:
            report["commit"], report["source"] = report["stamp"], "build"
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
