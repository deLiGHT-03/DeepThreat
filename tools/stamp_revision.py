"""Stamp this checkout's revision into the deployment, for a host that hands one over to nobody.

A host that exports a revision variable has it read for nothing; a host that exports none leaves
its copy answering that it does not know which commit it is running, which is the one thing that
tells a current copy from a stale one. Such a host usually has what is needed *while it builds*
even when its running copy has nothing: the build runs in the checkout, and a container or a CI
runner almost always has a ``.git`` beside the files it is packaging. So this writes down what
``services.build_info`` could work out at build time, and the file travels inside the deployment
for the running copy to read.

    python tools/stamp_revision.py                 # write services/revision.txt
    python tools/stamp_revision.py --file build.txt
    python tools/stamp_revision.py --strict        # fail the build if it cannot be stamped

It answers from the same places ``build_info`` does, in the same order -- the host's own variable
first, the checkout after that -- so whatever a build can work out is what gets written down.
``services/revision.txt`` is not committed: it names one build, and a stamp in the repository
would go stale the moment somebody committed again.

Two deliberate choices about failure. By default a build that cannot stamp still succeeds: a
health check that says "unknown" is worse than a health check that names the commit, but it is
not worse than a deploy that does not happen. ``--strict`` turns that around for a pipeline that
would rather hear about it. And a stamp that is not a commit -- a truncated variable, a template
that never got filled in -- is written by nothing here and refused by nothing here; the reader
checks its shape rather than reporting it as a revision.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import build_info  # noqa: E402

#: Where the stamp goes, beside the module that reads it. The name is in .gitignore for the same
#: reason it is written here: it belongs to one build and not to the repository.
DEFAULT_FILE = Path(build_info.__file__).with_name("revision.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=str(DEFAULT_FILE),
                        help=f"where to write the stamp (default: {DEFAULT_FILE.name} beside "
                             f"services/build_info.py)")
    parser.add_argument("--strict", action="store_true",
                        help="exit 2 instead of 0 when this build cannot be stamped")
    args = parser.parse_args()

    report = build_info.describe()
    commit = str(report.get("commit") or "").strip()
    source = str(report.get("source") or "unknown")
    if not commit or source in ("unknown", "build"):
        # Nothing to write down, or a stamp from a build already in this tree: writing it again
        # would say the same thing twice and hide which build it came from.
        print(f"nothing to stamp: this build knows no revision (source {source})", file=sys.stderr)
        return 2 if args.strict else 0

    if report.get("dirty"):
        print("warning: the working tree has moved on from that commit, so the stamp names the "
              "commit and not the files beside it", file=sys.stderr)

    path = Path(args.file).resolve()
    try:
        path.write_text(commit + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return 2 if args.strict else 0

    print(f"stamped {commit} (from {'the checkout' if source == 'checkout' else source}) into "
          f"{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
