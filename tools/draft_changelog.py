"""Draft the notebook's next release from the commits that have not been written down yet.

``services/changelog.py`` is what the What's new panel shows, and it is written by hand because it
has to be: a commit says what changed in the code, and the notebook says what changed for the
person playing in the league. That translation is the whole job here. It gathers the commits
after ``COVERED_THROUGH`` -- the last commit the changelog already speaks for -- hands their
messages to the same Gemini model Ask AI uses, and asks for the reader's version: one line per
change, behind the word for what kind of change it is, in the voice the file is written in.

    python tools/draft_changelog.py                    # draft what is not written down yet
    python tools/draft_changelog.py --since HEAD~6     # draft a range you name yourself
    python tools/draft_changelog.py --write            # put the draft at the top of the file

A draft is a draft. Numbers are the one thing that cannot be invented here -- a line says "reads
~60 things instead of 311" because a commit message says so -- so the tool tells the model to use
only figures that appear in the messages, prints the commits behind the draft beside it, and warns
about any line that still carries a file name, because a reader has no way to check one.

With no key, or with a model that will not answer, the tool still does the gathering half: it
prints the commits and a rough draft made from their own subjects, marked as rough. A rough draft
is never written to the file: the notebook is read by people playing in leagues, and a commit
subject is the one thing its lines must not be.

Exit codes: 0 a draft was produced, 1 there is nothing to draft, 2 the repository or the module
could not be read, or the draft had to be rough.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The draft is read with · and — in mind, so do not let a cp1252 console mangle it.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from services.changelog import COVERED_THROUGH, KINDS  # noqa: E402

DEFAULT_FILE = ROOT / "services" / "changelog.py"

#: The separators used to read the log in one call: a unit separator between fields and a record
#: separator between commits, neither of which occurs in a commit message.
_FIELD, _RECORD = "\x1f", "\x1e"

#: The trailers every commit in this repository carries, which say nothing about what changed.
_TRAILERS = re.compile(r"^\s*(?:🤖.*|Co-Authored-By:.*|Generated with .*)\s*$", re.MULTILINE)

#: What a line must not contain: a reader cannot open a file, so a file name in one is a mistake
#: the tool can point at rather than something to be discovered in the panel.
_CODEISH = re.compile(r"\b[\w./-]+\.(?:py|js|css|html|json|md|txt|ya?ml|spec)\b"
                      r"|\b(?:services|templates|static|tools|packaging)/\w+"
                      r"|\b[a-z]+_[a-z_]+\(\)")

#: What a rough draft guesses from, when there is no model to ask: the words each kind of change
#: is written with. The first pattern that matches wins, so the specific ones come first.
_ROUGH_HINTS = (
    ("performance", r"\b(?:fast|faster|slow|ms\b|seconds|reads?|bytes|cache|cached|pool|worker|"
                    r"latenc|optimi[sz]|speed|size|smaller|memory|cpu|quer(?:y|ies)|requests?)"),
    ("visual", r"\b(?:style|styles|colour|color|theme|palette|icon|layout|look|spacing|badge|"
               r"chip|design|font|contrast|align)"),
    ("functionality", r"\b(?:add|adds|adding|new|support|allow|enable|show|filter|search|open|"
                      r"toggle|tab|button|export|save|remember|sort|switch)"),
)

_MARKER = re.compile(r"^\s*[-*•]?\s*\**\s*(performance|visual|functionality|also)\s*\**\s*"
                     r"[|:]\s*(.+)$", re.IGNORECASE)
_TITLE = re.compile(r"^\s*\**\s*TITLE\s*\**\s*[|:]\s*(.+)$", re.IGNORECASE)

SYSTEM_INSTRUCTION = """\
You write the "What's new" notebook of DeepThreat, a fantasy football league app. The notebook is
read by the people playing in leagues on it, not by its developers, and it is read to find out
what changed and whether they care.

You are given developers' commit messages. Turn them into the reader's version.

Rules, in order of importance:
1. Write about what a person using the app would notice: a page that is faster, something that
   looks different, something the app can do now. How it was done is not a line — no file names,
   no class or function names, no "refactor", no "single-flight", no branch names, no counts of
   threads or requests the code makes.
2. Use a figure only if a commit message contains it, and use it exactly as written there. Never
   estimate, average, round or invent a number. A change whose messages carry no figure gets a
   line with no figure in it. A figure a message corrects, retracts or says was never actually
   measured is not a figure to use: these messages are honest about which of their own numbers
   hold, and a line that repeats a disowned one is worse than a line with no number at all.
3. Where a message sets two figures against each other with an arrow or a "to" — "1.33 s ->
   0.78 s", "309 to 60 reads" — the second is what it became and the first is what it was, so the
   line says "0.78 s, down from 1.33 s" and never the other way round.
4. Where the messages give a figure for a change, that figure is the line: "History opens in about
   5 seconds instead of 13" says something and "History loads much faster" says nothing. Never
   write "significantly", "much", "greatly" or "dramatically" in place of a number the messages
   already contain.
5. One line per change, a few words and never more than one sentence — about twenty words. Two
   changes a reader would experience as one change are one line.
6. Give every line one of these four kinds:
   - performance: something that was slow or heavy and is not any more.
   - visual: something that looks different, or something new to look at.
   - functionality: something the app can do now that it could not before, or does differently.
   - also: a change nobody outside would notice, worth one quiet line and no more.
7. Between two and eight lines: the changes a reader would care about first, the quibbles last.
8. Do not greet, do not explain yourself, and do not mention commits, branches or messages.

Answer with the title and then the lines, in exactly this form and nothing else:

TITLE: <the release's headline, in the reader's words, no more than eight words>
performance | <line>
visual | <line>
also | <line>
"""


def _git(*args: str) -> str:
    """Run a git command in the repository and return its output, or raise RuntimeError."""
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "git failed")
    return result.stdout


def _display(path: Path) -> str:
    """A path as the fewest characters that still say where it is."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _clean_body(body: str) -> str:
    """A commit's message without the trailers this repository signs every commit with."""
    text = _TRAILERS.sub("", body or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def commits_since(anchor: str, limit: int) -> list[dict]:
    """Every commit after ``anchor``, oldest first, as dicts of sha, date, subject and body.

    Oldest first because the messages are read as a story: a later commit often says what an
    earlier one turned out to be, and the draft reads better with both in hand. The log is read
    newest-first and turned around here, because ``--max-count`` counts before ``--reverse`` does
    and would otherwise hand back the oldest commits of the history rather than the newest.
    """
    revision = f"{anchor}..HEAD" if anchor else "HEAD"
    raw = _git("log", f"--max-count={limit}",
               f"--format=%H{_FIELD}%ad{_FIELD}%s{_FIELD}%b{_RECORD}", "--date=short", revision)
    commits = []
    for record in raw.split(_RECORD):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, day, subject, body = (record.split(_FIELD) + ["", "", "", ""])[:4]
        commits.append({"sha": sha.strip(), "date": day.strip(), "subject": subject.strip(),
                        "body": _clean_body(body)})
    commits.reverse()
    return commits


def _prompt(commits: list[dict]) -> str:
    """The commit messages, laid out for the model, oldest first."""
    blocks = [f"### {commit['subject']}\n{commit['body'] or '(no further detail)'}"
              for commit in commits]
    return ("These are the commit messages, oldest first.\n\n" + "\n\n".join(blocks) +
            "\n\nWrite the notebook's next release from them.")


def _parse(answer: str) -> tuple[str, list[tuple[str, str]]]:
    """Read the model's answer: a title and (kind, line) pairs, in the file's own kinds.

    Anything else is dropped rather than guessed at, because a line the notebook cannot label is
    a line that would reach a reader under the wrong word.
    """
    title, lines = "", []
    known = {kind for kind, _ in KINDS}
    for raw in (answer or "").splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        heading = _TITLE.match(line)
        if heading and not title:
            title = heading.group(1).strip().strip('"').strip("'")
            continue
        match = _MARKER.match(line)
        if not match:
            continue
        kind = match.group(1).lower()
        text = match.group(2).strip().strip('"').strip("'")
        if kind in known and text:
            lines.append((kind, text))
    return title, lines


def draft_with_model(commits: list[dict]) -> tuple[str, list[tuple[str, str]], str]:
    """Ask the model for a draft. Returns the title, the lines, and the model's name."""
    from google.genai import types

    from services.ask_model import MODEL, client

    # The client is held in a name rather than built inline: it owns the connection the request
    # goes out on, and a temporary that nothing refers to can be collected before it is used.
    gemini = client()
    answer = gemini.models.generate_content(
        model=MODEL,
        contents=_prompt(commits),
        config=types.GenerateContentConfig(
            temperature=0.4,
            system_instruction=SYSTEM_INSTRUCTION,
            # There are no tools to call here, and asking for a plain answer is the whole job:
            # left on, the SDK offers the model a round of automatic calls and says so on stderr.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    title, lines = _parse(getattr(answer, "text", "") or "")
    return title, lines, MODEL


def _rough_kind(commit: dict) -> str:
    """Which kind a commit looks like, from the words it is written with."""
    text = f"{commit['subject']} {commit['body']}".lower()
    for kind, pattern in _ROUGH_HINTS:
        if re.search(pattern, text):
            return kind
    return "also"


def rough_draft(commits: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """A draft made from the commit subjects themselves, for when there is no model to ask.

    Rough on purpose and named so: the subjects are the developers' words, which is the one thing
    a line in the notebook must not be. It is here so the tool still gathers and still points at
    the shape of the answer rather than stopping at an error.
    """
    lines = []
    for commit in commits:
        text = commit["subject"].strip().rstrip(".")
        if text:
            lines.append((_rough_kind(commit), f"{text[0].lower()}{text[1:]}."))
    return "Draft from the commit subjects", lines[:8]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _entry_source(pair: tuple[str, str], width: int = 98) -> str:
    """One ``(kind, "text")`` entry, wrapped the way the file wraps its own."""
    kind, text = pair
    indent = " " * 13
    chunks, current = [], ""
    for word in _escape(text).split(" "):
        candidate = f"{current} {word}".strip()
        if current and len(indent) + len(candidate) + 4 > width:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    lines = [f'("{kind}",']
    for index, chunk in enumerate(chunks):
        lines.append(f'{indent}"{chunk} "' if index < len(chunks) - 1 else f'{indent}"{chunk}"),')
    return "\n".join(lines)


def _release_source(day: str, title: str, lines: list[tuple[str, str]]) -> str:
    """The whole release as it would be written in the file: a dict a person can then edit."""
    out = [f'        "date": "{_escape(day)}",', f'        "title": "{_escape(title)}",',
           '        "entries": [']
    out.extend("            " + _entry_source(line) for line in lines)
    out.append("        ],")
    return "    {\n" + "\n".join(out) + "\n    },\n"


def _insert(text: str, block: str) -> str:
    """Put a drafted release at the top of RELEASES."""
    anchor = "RELEASES = [\n"
    if text.count(anchor) != 1:
        raise RuntimeError("no single RELEASES list to draft into")
    return text.replace(anchor, anchor + block, 1)


def _move_anchor(text: str, sha: str) -> str:
    """Point COVERED_THROUGH at the commit the draft now covers."""
    pattern = re.compile(r'^(COVERED_THROUGH = )"[^"]*"', re.MULTILINE)
    if not pattern.search(text):
        raise RuntimeError("no COVERED_THROUGH line to move")
    return pattern.sub(lambda match: f'{match.group(1)}"{sha}"', text, count=1)


def _reload(path: Path):
    """Import a module from its path, to prove the file that was just written still works.

    The loader is named rather than inferred from the file's suffix: what is being checked is the
    staging copy, which is deliberately not called ``.py`` so that nothing else picks it up.
    """
    name = "_drafted_changelog"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


def _report(title: str, lines: list[tuple[str, str]], model_used: str) -> None:
    """Print the draft the way the panel groups it, and flag what a reader cannot check."""
    labels = dict(KINDS)
    order = {kind: index for index, (kind, _) in enumerate(KINDS)}
    width = max((len(labels[kind]) for kind, _ in lines), default=0)
    print(f"\n{title}" + (f"   [drafted by {model_used}]" if model_used else "   [ROUGH]"))
    for kind, text in sorted(lines, key=lambda line: order[line[0]]):
        print(f"  {labels[kind]:<{width}}  {text}")
    suspect = [text for _, text in lines if _CODEISH.search(text)]
    if suspect:
        print("\n  ! a line names something a reader cannot open -- edit it before it ships:")
        for text in suspect:
            print(f"      {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="",
                        help="the commit to draft from (default: COVERED_THROUGH in the module)")
    parser.add_argument("--limit", type=int, default=60, help="the most commits to read")
    parser.add_argument("--write", action="store_true",
                        help="put the draft at the top of the file and move the anchor to HEAD")
    parser.add_argument("--file", default=str(DEFAULT_FILE),
                        help="the changelog module to draft into (default: services/changelog.py)")
    args = parser.parse_args()

    path = Path(args.file).resolve()
    anchor = args.since or COVERED_THROUGH
    try:
        head = _git("rev-parse", "--short", "HEAD").strip()
        commits = commits_since(anchor, args.limit)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"could not read the repository: {exc}", file=sys.stderr)
        return 2

    named = " (the module's COVERED_THROUGH)" if not args.since else ""
    print(f"drafting from {anchor or 'the first commit'}..{head}{named}: "
          f"{len(commits)} commit{'' if len(commits) == 1 else 's'}")
    if not commits:
        print(f"\nnothing to draft: every commit is already written down in {_display(path)}")
        return 1
    for commit in commits:
        print(f"  {commit['sha'][:8]}  {commit['date']}  {commit['subject']}")

    title, lines, model_used, failure = "", [], "", ""
    try:
        title, lines, model_used = draft_with_model(commits)
    except Exception as exc:  # noqa: BLE001 - any failure here means drafting it roughly instead
        failure = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    if not lines:
        title, lines = rough_draft(commits)
        model_used = ""
        if failure:
            print(f"\nthe model could not draft this ({failure}), so these are the commit "
                  "subjects in their own words")

    _report(title, lines, model_used)
    block = _release_source(date.today().isoformat(), title, lines)
    print("\n--- what goes in the file ---\n")
    print(block, end="")

    if args.write and not model_used:
        print(f"\nnot writing that: a rough draft is a developer's words, and the notebook is "
              f"read by the people playing\nedit {_display(path)} by hand, or fix whatever stopped "
              "the model and run again", file=sys.stderr)
        return 2

    if args.write:
        # The draft is written beside the module and imported there before it is allowed to
        # replace it: a release is only a release if the file that holds it still loads, and the
        # one thing worse than a draft nobody edited is a module that no longer imports.
        staging = path.with_name(path.name + ".drafted")
        try:
            updated = _move_anchor(_insert(path.read_text(encoding="utf-8"), block), head)
            staging.write_text(updated, encoding="utf-8")
        except (OSError, RuntimeError) as exc:
            print(f"\ncould not write {_display(path)}: {exc}", file=sys.stderr)
            return 2
        try:
            written = _reload(staging).releases()[0]
        except Exception as exc:  # noqa: BLE001 - a draft that will not import is not a release
            staging.unlink(missing_ok=True)
            print(f"\nthat draft would not import, so {_display(path)} is untouched: {exc}",
                  file=sys.stderr)
            return 2
        staging.replace(path)
        print(f"\nwritten to {_display(path)}: {written['date_label']} · {written['title']}, and "
              f"COVERED_THROUGH is now {head}\nread the lines, edit them, then commit")
        return 0

    print(f"\nedit these, then paste them at the top of RELEASES in {_display(path)}, or run "
          "again with --write")
    return 0 if model_used else 2


if __name__ == "__main__":
    raise SystemExit(main())
