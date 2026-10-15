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
COVERED_THROUGH = "f78edb6"

#: Every release, newest first. See the module docstring for what belongs in one.
RELEASES = [
    {
        "date": "2026-10-15",
        "title": "The comparison sits beside every Pace board",
        "entries": [
            ("visual",
             "The board of every position reads its comparison beside it now, on the same line as "
             "the figures: the panel used to sit above that board, and it now takes a column of "
             "the page beside it, as it already did on a board narrowed to one position."),
            ("visual",
             "A board narrowed to one position fills the width it has and its figures begin on "
             "the same line as the Compare panel beside them — the board reaches the panel "
             "instead of stopping short with a strip of empty page between the two."),
            ("also",
             "The board of every position carries twelve columns where a board of one carries at "
             "most nine, so its columns sit closer together to leave the panel room beside them. "
             "Every column is still there."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A back's pace reads in scrimmage yards",
        "entries": [
            ("visual",
             "A running back's pace now carries scrimmage yards — his rushing and receiving yards "
             "in one figure — in the place his rushing attempts used to hold, at the right of his "
             "receiving yards. On the board of every position it is a column of its own for backs "
             "and a dash for everyone else, and it sorts like any other column."),
            ("also",
             "The pace button on a back's card and an Ask AI answer about him show the same "
             "figure, so scrimmage yards is what they carry in place of attempts too."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "One Group View switch, standing over the week's matchups",
        "entries": [
            ("visual",
             "On a matchup week the switch that widens the lineup slots into position groups has "
             "moved down to sit over the matchups it changes, instead of up among the page tabs -- "
             "it is the one control over the whole week and now reads as one."),
            ("also",
             "Broad View is called Group View everywhere now: the same view, under the name that "
             "says what it does."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A comparison is read beside the board it is about",
        "entries": [
            ("visual",
             "Compare is now a column down the right of a Pace board that is narrowed to a position, "
             "so the two players and the board they are read against are in view at once. The page "
             "is no wider for it: the board and the comparison share the width the page already "
             "had, and the board, not the page, is what scrolls sideways when the two of them need "
             "more room than there is."),
            ("visual",
             "The comparison's two boxes are stacked rather than set side by side, which is what "
             "leaves the column narrow enough for a full board to fit beside it -- the compromise "
             "is made on the panel's width rather than the board's."),
            ("also",
             "Compare is always shown now: there is nothing left to fold away and open again."),
            ("also",
             "A page too narrow for the board and the comparison side by side keeps Compare "
             "above the board instead, where it was."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A Pace board is as wide as its columns need",
        "entries": [
            ("visual",
             "The Pace board is now only as wide as the columns on it, instead of stretching to the "
             "page's width and sharing out the room its figures did not need: a board of a few "
             "positions is a small board with its numbers close together, and a board of every "
             "position the league has scored still reaches the page's edge, and scrolls sideways "
             "when the page is narrower than it is."),
            ("visual",
             "Games played is the width of its own two letters and a figure or two, so a wide "
             "board reads as figures rather than as a column of empty space with a digit at one "
             "end of it."),
            ("visual",
             "The column of names is capped at the width a name can be read at, so the longest "
             "name on a board ends in an ellipsis instead of widening it, and the whole of that "
             "name is there when the pointer rests on it."),
            ("also",
             "The button that saves a Pace board as an image follows the board's edge rather than "
             "staying at the page's, and the note under the board is set from the board's own left "
             "edge."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A Pace board marks what a player is worth",
        "entries": [
            ("visual",
             "The points and points-a-game columns on the Pace board are drawn in the same dark "
             "cell the rest of the app gives a score — the box around a Player Data table's points "
             "per game, and the one around a player's card total — so the two columns that say "
             "what a player is worth are the two the eye lands on."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A Pace board takes its position as its title",
        "entries": [
            ("visual",
             "A Pace board narrowed to a position now names it as the table's own title — "
             "centered over the columns and set large enough to read as what the board below it "
             "is — rather than as a small label tucked into the top corner of it."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A player comparison is the table and nothing said twice",
        "entries": [
            ("visual",
             "The comparison no longer writes the table out again in a paragraph underneath it: "
             "the card is the two names, the stats and their figures with the leader of each row "
             "marked, so what gets saved is the table rather than the table and an account of it "
             "-- and the small print under its buttons is down to the one thing the figures cannot "
             "say for themselves."),
            ("visual",
             "The card is also narrower, and each figure now sits beside the stat it belongs to "
             "instead of out at the card's far edge -- and the two players' figures are each given "
             "the same room, so a value and the mark on the row's leader line up down the card "
             "instead of the two columns ending in different places."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "History has the season switcher every other page has",
        "entries": [
            ("functionality",
             "The season switcher now sits at the top of History, where the rest of the app keeps "
             "it, and picking a season moves you into that season rather than only narrowing what "
             "you are reading: the tabs follow you there, so Matchups, Player Data, Pace and Trades "
             "all open on the season you chose."),
            ("also",
             "The year chips over the record book are unchanged. They still read one season of the "
             "whole history, and still leave the season you are in where it was."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Group View and Defense are one choice on the Season table",
        "entries": [
            ("visual",
             "The Group View checkbox now sits beside Defense, over the Season table, instead "
             "of up among the tabs — and the two are one choice: turning one on dims the other, "
             "so the table can no longer be asked to be both at once."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "Compare lives on the Pace board now",
        "entries": [
            ("visual",
             "The Compare tab is gone and Pace has moved to sit beside Player Data, so the tabs "
             "now read Matchups, Player Data, Pace, Trades, History and Ask AI."),
            ("functionality",
             "Comparing two players no longer needs a tab of its own: the two name boxes sit "
             "above the Pace board, folded away until you search, and a search opens them onto "
             "the same side-by-side card of what each is on pace for. Fold it back and the "
             "board you were reading is still there."),
            ("also",
             "A comparison follows you when you switch season or league, and a link to the old "
             "Compare page opens the same comparison on the Pace board."),
        ],
    },
    {
        "date": "2026-10-14",
        "title": "A Pace board says which position it is showing",
        "entries": [
            ("visual",
             "A Pace board narrowed to a position now names it once at the top of the table — "
             "Quarterback, Running Back — instead of a two-letter label repeating down every row, "
             "and the room that column took is spent on the figures. All still labels each "
             "player, because its rows are not all one position."),
        ],
    },
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
             "yards, catches, scrimmage yards, touchdowns and turnovers."),
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
             "games — yards, catches, scrimmage yards, touchdowns, turnovers, points and PPG."),
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
             "Matchups by slot or Group View, Season records with True Record and luck, Player "
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
