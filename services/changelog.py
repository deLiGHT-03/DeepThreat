"""What has changed, written for the person playing in the league rather than for the person who
built the app.

The notebook beside Ask AI reads this file and nothing else, so this is the one place a change is
written down for the reader. One line per change: a few words, or a sentence when the sentence is
the whole of it. What belongs here is what somebody using DeepThreat would notice:

* **performance** -- a page that used to be slow and is not any more, with the number if there is
  a real one;
* **visual** -- something that looks different, or something new to look at;
* **functionality** -- something the app can do now that it could not before;
* **also** -- the smaller things a reader would still notice, kept quiet and last.

What does not belong here is how it was done, and what belongs here even less is work only the
person who built the app would see. No file names, no class names, no "single-flight", no thread
counts, no commits, no deploys, no hosting: a line says "History opens in 5s instead of 13", not
"finished seasons are rebuilt from a derived digest". A change nobody playing in a league would
notice is not a line at all -- a release records what a reader gets, so work that reaches them as
nothing stays out of it rather than being explained to them. Lines are read, not studied: a few
words, one sentence at the most.

Adding a release is one dict at the top of :data:`RELEASES`: a date, a headline in the reader's
words, and its lines as ``(kind, text)`` pairs. Newest first, and the date is the only order there
is -- the app has no version numbers to keep in step. A release can be drafted from the commits
behind it with ``python tools/draft_changelog.py``, which reads :data:`COVERED_THROUGH` to know
which commits those are and prints lines to edit rather than a file to trust.
"""

from __future__ import annotations

import datetime

#: The kinds of change, in the order a release lists them, each with the word the chip prints.
#: "also" goes last on purpose: it is the footnote of a release, not one of its three subjects. A
#: kind that is not one of these is a mistake in this file rather than a release to guess at, and
#: :func:`releases` refuses to serve it.
KINDS = (
    ("performance", "Performance"),
    ("visual", "Visual"),
    ("functionality", "Functionality"),
    ("also", "Also"),
)

#: The newest commit this file already speaks for. Everything committed after it is work the
#: notebook has not been told about yet, and that is what ``tools/draft_changelog.py`` drafts
#: from; the tool moves this along when it writes a release, so it is kept honest by being written
#: rather than remembered. Nothing reads it while the app runs -- it is a note to that tool.
COVERED_THROUGH = "20b877c"

#: Every release, newest first. See the module docstring for what belongs in one.
RELEASES = [
    {
        "date": "2026-10-14",
        "title": "A year in History is a look, not a move",
        "entries": [
            ("functionality",
             "Choosing a year in History now changes only the record book you are reading: the "
             "tabs beside it, the league switcher and the address all stay on the season you are "
             "in, so a look back at an older year no longer opens the rest of the app in that "
             "year's league."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A Pace board remembers how it was read",
        "entries": [
            ("functionality",
             "Sort a Pace board by any column and narrow it to a position, and the address says "
             "so: paste it into a league chat and it opens as the board you were reading — the "
             "same rows in the same order, with the column it is read by named above the table."),
            ("also",
             "Changing season or league keeps both, because it is the same board on another "
             "set of games."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Compare boxes finish the name for you",
        "entries": [
            ("functionality",
             "Start typing a player's name in a compare box and the rest of it appears faintly "
             "after what you have typed; Tab, Enter or a click on the faint part takes it, so a "
             "name does not have to be typed out in full."),
            ("also",
             "The suggestion is always a player your own league has held — the same names the "
             "comparison can answer for — the ones on pace for the most suggested first."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A Pace board for the whole league",
        "entries": [
            ("functionality",
             "A Pace tab: every player the league has scored, and what each is on pace for over "
             "a full season in the stats his position is judged by — points and points a game, "
             "passing and rushing yards, carries, catches, touchdowns and turnovers."),
            ("visual",
             "Click any column to sort by it and any position chip to narrow it, with a dash "
             "where a stat is not one that position is measured by."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Pace rankings show a player's whole line",
        "entries": [
            ("functionality",
             "Ask who is on pace for the most of something and the table now carries each "
             "player's whole line — the yards, catches and touchdowns his own position is "
             "measured by, beside his points — so it can be read across as well as down."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Compare a player from his own card",
        "entries": [
            ("functionality",
             "A generated pace now has a box under it: name a second player and his pace is "
             "set beside the first, on the stats they share, with whoever is ahead on each one "
             "marked."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Pace rankings cover every stat a position has",
        "entries": [
            ("functionality",
             "Ask who is on pace for the most rushing yards, catches or touchdowns and it "
             "answers for this season the same way it does for points."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Share a comparison from its own page",
        "entries": [
            ("functionality",
             "Compare tab: name two players and their paces sit side by side, on a page whose "
             "address can be pasted into a league chat, and a camera button saves the table as "
             "an image."),
            ("visual",
             "The player ahead on each stat is marked in the comparison table, so the two "
             "columns can be read down a row instead of counted up."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Ask AI can rank the league by what it is on pace for",
        "entries": [
            ("functionality",
             "Ask who is on pace for the most points and it answers for this season, ranking "
             "every player the league has scored by his pace — not the best season anyone has "
             "already had."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Ask AI can weigh two players against each other",
        "entries": [
            ("functionality",
             "Name two players and Ask AI puts their paces side by side for a trade, on the "
             "stats both of them are measured by, projecting each over the same 17 games."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Ask AI can say what a player is on pace for",
        "entries": [
            ("functionality",
             "Ask AI knows what a player is on pace for: ask it and it projects his season over "
             "17 games, stat by stat, scoring the points the same way this league does."),
        ],
    },
    {
        "date": "2026-10-13",
        "title": "A card can say what a player is on pace for",
        "entries": [
            ("functionality",
             "Player cards have a Generate Pace button: one press projects the season over 17 "
             "games — yards, carries or catches, touchdowns, turnovers, points and PPG."),
        ],
    },
    {
        "date": "2026-10-13",
        "title": "Ask AI knows who was on the other side",
        "entries": [
            ("performance",
             "Ask AI answers in about a second instead of a minute or more."),
            ("functionality",
             "Ask AI can say how one player has done against any NFL team — every game, and the "
             "average."),
            ("also",
             "A model that is too busy to answer is passed over for another instead of failing "
             "the question."),
        ],
    },
    {
        "date": "2026-10-11",
        "title": "Faster History, and a notebook of what changed",
        "entries": [
            ("performance", "History opens in about 5s instead of 13."),
            ("performance", "Pages that read a lot from Sleeper load faster too."),
            ("visual", "A notebook beside Ask AI opens this list."),
            ("functionality",
             "It opens from any page, and closes with Escape or a click away."),
        ],
    },
    {
        "date": "2026-10-11",
        "title": "Lighter pages, a better-behaved public address",
        "entries": [
            ("performance", "The logo is 284 KB instead of 840 KB — the same picture."),
            ("performance",
             "Repeat visits load faster: stylesheets and scripts are kept until they change."),
            ("functionality",
             "On a hosted copy your connection is remembered by your browser, never by the host."),
            ("functionality",
             "A public copy turns search engines away and slows anyone asking for far too much."),
            ("also", "Saved screenshots include team logos."),
            ("also", "A refused request says how long the wait is."),
        ],
    },
    {
        "date": "2026-10-10",
        "title": "The same app, on the web",
        "entries": [
            ("functionality",
             "DeepThreat runs on a host: connect with your Sleeper username and every page works "
             "in a browser."),
            ("functionality", "Nothing to set up beside it."),
            ("functionality",
             "League switcher, season chips, colour themes and Preload Data all came across "
             "unchanged."),
            ("also", "Preload Data makes the pages after it instant."),
        ],
    },
    {
        "date": "2026-10-09",
        "title": "Where it started",
        "entries": [
            ("functionality",
             "Matchups by slot or Broad View, Season records with True Record and luck, Player "
             "Data by week or season, Trades, History — every season the league has played."),
            ("functionality",
             "Trades searches every season by player, team, manager or pick; a pick opens what "
             "became of it."),
            ("functionality",
             "Ask AI answers from the league's own records and lists the lookups behind each "
             "answer. It needs a free Gemini key."),
            ("visual",
             "Six colour themes besides the default, player cards, your team marked YOU, and a "
             "camera button that saves a matchup or a table as a picture."),
            ("also",
             "Impact Players is built but hidden for now — its page still opens at its own "
             "address."),
        ],
    },
]


def _date_label(value: str) -> str:
    """A release's date the way a person reads it: ``11 October 2026``.

    Spelled out here rather than in the template because the platform's own date formatting is
    the thing that gets this wrong: ``%-d`` is not portable, and a locale-aware month name would
    arrive in whatever language the host happens to be set to.
    """
    try:
        parsed = datetime.date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{parsed.day} {parsed.strftime('%B')} {parsed.year}"


def _lines(release: dict) -> list[dict]:
    """One release's changes, each with the word its chip prints.

    Ordered by :data:`KINDS` rather than by the order they happen to be written in, so a release
    reads performance first, visual next, functionality after that and the quieter notes last --
    stable from one release to the next, which is what makes the list scannable.
    """
    labels = dict(KINDS)
    rank = {kind: index for index, (kind, _) in enumerate(KINDS)}
    for kind, _ in release["entries"]:
        if kind not in labels:
            raise ValueError(
                f"changelog line in {release['title']!r} has kind {kind!r}, "
                f"which is not one of {sorted(labels)}"
            )
    return [
        {"kind": kind, "label": labels[kind], "text": text}
        for kind, text in sorted(release["entries"], key=lambda line: rank[line[0]])
    ]


def releases() -> list[dict]:
    """Every release, newest first, ready to draw: dates spelled out, each line labelled.

    A kind that is not in :data:`KINDS` raises here rather than reaching a page. The app builds
    this at startup, so a typo in this file stops the app instead of quietly drawing a line with
    a chip that has nothing written on it.
    """
    return [
        {
            "date": release["date"],
            "date_label": _date_label(release["date"]),
            # What the reader has already seen: a release is new until this stamp is the one the
            # browser last opened the notebook on. The title is in it because two releases can
            # share a day, when the second is written the same evening as the first.
            "stamp": f"{release['date']}|{release['title']}",
            "title": release["title"],
            "lines": _lines(release),
        }
        for release in RELEASES
    ]


def latest() -> dict | None:
    """The newest release, or None in a file with nothing in it yet."""
    prepared = releases()
    return prepared[0] if prepared else None
