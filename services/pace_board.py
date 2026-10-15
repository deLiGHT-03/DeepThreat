"""The Pace board: what every player in this league is on pace for, in one sortable table.

The figures are read once for the whole league by :func:`services.league_tools.pace_lines` --
the same read Ask AI ranks a pace question from, and the same arithmetic behind the pace button
on a player's card -- so a number on this page, in an answer and on a card are one projection of
one set of games. What the page adds is what a question cannot: every stat of every player at
once, in a table a manager can reorder by whichever of them he is thinking about.

Where the board is read from and which way it is read are part of the address rather than
something the page remembers for itself, so a board can be pasted into a league chat and arrive
as the board it was: ``position`` narrows it to one chip, ``sort`` names the column by the
abbreviation its own heading prints, and ``dir`` says which way round it is read. A column this
board does not draw -- a stat the position in view is not measured by, or a word from a link
somebody edited -- is not an error and not a guess: it opens the board the way the board opens,
which is its first stat column, highest first.

A pace is chiefly points, and the board opens on them, but it is the stats as a whole: each row
carries the stats that player's own position is measured by, and a stat a position is not
measured by is a dash in his row rather than a zero, because he has none of it rather than none
of it so far.

Two of those stats are read with the work that earned them beside them, faded: a back's rushing
yards have his attempts to their left, and his catches have his targets as a receiver's or a tight
end's do, exactly as :data:`services.player_pace.CONTEXT_STATS` sets them out. They are
drawn on a board narrowed to that position and nowhere else -- a board of every position leaves
them out, because a reader looking at a whole league wants the columns he scores by and not a
carry count for every one of them, and a board of one position has the room to say what the
figure beside it was made of.

The board carries one thing besides its own figures: the two backs at the top of this same read,
which is the comparison the panel beside it opens on. It is worked out here because here is where
the season has already been read once, and a second read of it for two names would be the whole
of the board's cost paid twice for a line of the page.
"""

from __future__ import annotations

from collections import Counter

from services.league_tools import pace_lines
from services.player_pace import (SEASON_GAMES, beside_note, context_rows, context_stats,
                                  pace_figure, stat_order, stat_table)
from services.sleeper import SleeperError

#: The position the panel's opening comparison is drawn from: a back, because the two of them are
#: read down the same column of stats and the figures are the ones a trade argument is about.
EXAMPLE_POSITION = "RB"


def _example_players(players: list[dict]) -> list[str]:
    """The two backs this league has scored the most, as names to open the comparison with.

    The board's read already holds every player's points pace, so the example costs no second
    read of the season: it is the top two of the backs in it, which is the comparison a reader
    who has never asked for one can be shown without his typing anything. A name that appears
    twice in the league is left out rather than offered, because the lookup behind the boxes
    refuses a name that matches more than one player and an opening that says so would be an
    example of nothing. A league with fewer than two backs to choose from has none, and the
    panel opens empty as it did before.
    """
    counted = Counter(str(who["name"]) for who in players)
    ranked: list[tuple[float, str]] = []
    for who in players:
        if str(who["position"]).upper() != EXAMPLE_POSITION:
            continue
        name = str(who["name"])
        if counted[name] != 1:
            continue
        points = next((row["pace"] for row in who["line"] if row["abbr"] == "PTS"), None)
        if points is None:
            continue
        ranked.append((float(points), name))
    # Most points first, and a tie broken by the name so the same two come back every time.
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in ranked[:2]]

#: The positions the board offers by name, in the order its chips read. A position the league has
#: scored that is not named here still gets a chip, so a league that starts a kicker can look at
#: his points without this list having to know about kickers.
NAMED_POSITIONS = ("QB", "RB", "WR", "TE")

#: The three columns that come before the stats, each as the table draws it: ``key`` is the word
#: an address names it by, ``label`` the heading it prints, ``type`` whether it is read as a
#: figure or as a name, ``title`` the note under the heading, ``saying`` what the sentence above
#: the table calls it, and ``field`` the value in a row it stands for. A stat's own key is the
#: abbreviation its heading prints, so every column a reader can sort by has a name he can see.
FIXED_HEADS = (
    {
        "key": "player", "label": "Player", "type": "text", "title": "",
        "saying": "name", "field": "name",
    },
    {
        "key": "pos", "label": "Pos", "type": "text", "title": "",
        "saying": "position", "field": "position",
    },
    {
        "key": "gp", "label": "GP", "type": "number",
        "title": ("Games he has actually played: a week this league scored and that Sleeper "
                  "recorded him on the field for"),
        "saying": "games played", "field": "games",
    },
)

#: The two ways a column can be read, as an address spells them.
WAYS = ("asc", "desc")


def _heads(columns: list[dict]) -> list[dict]:
    """The table's headings, the stats' after the three fixed ones, in the order it draws them.

    A stat heading stands for a place in a row's own cells rather than for a value on the row,
    because the stats a row carries are the ones its position is measured by -- and it is the
    cell's own figure that is sorted, which is the projection the column shows. A context column
    is marked as one here rather than worked out again by the page, which is what lets the page
    set the faded column quiet where it draws it and leave every other column as it is.
    """
    heads = [dict(head) for head in FIXED_HEADS]
    labels = {column["abbr"]: column["label"] for column in columns}
    for index, column in enumerate(columns):
        # The faded column's own note says what it is read beside, because that is the whole of
        # what it is there for: a heading of two letters over a figure nobody scores is a puzzle
        # otherwise. Its ``saying`` is still its own name, which is what the sentence above the
        # table calls the board by when a reader sorts on it.
        beside = labels.get(column.get("beside") or "")
        heads.append({
            "key": column["abbr"].lower(), "label": column["abbr"], "type": "number",
            "title": (beside_note(column["label"], beside)
                      if beside and column.get("context") else column["label"]),
            "saying": column["label"], "cell": index,
            "context": bool(column.get("context")),
        })
    return heads


def _with_context(columns: list[dict], beside: list[dict]) -> list[dict]:
    """The columns with each context stat inserted directly to the left of the stat it stands by.

    The place comes from the stat's own definition -- the abbreviation it is read before -- rather
    than from a position in a list written down here, so where a faded column sits is the same
    fact wherever it is asked for: a back's attempts land beside his rushing yards on this board
    because that is the stat they are defined against, not because this is where they were put.
    A context stat whose stat is not a column of this board -- one that could never be drawn -- is
    left off rather than hung at the end of the table with nothing to sit by.
    """
    placed = [dict(column) for column in columns]
    for stat in beside:
        anchor = next((index for index, column in enumerate(placed)
                       if column["abbr"] == stat["before"]), None)
        if anchor is None:
            continue
        placed.insert(anchor, {"abbr": stat["abbr"], "label": stat["label"],
                               "context": True, "beside": stat["before"]})
    return placed


def _value(row: dict, head: dict):
    """A row's own figure in one column: a number, a name, or None where it has none.

    A dash is not a zero. The cell a player's position is not measured by holds no figure of his,
    which is not the same as holding none so far, so it comes back as None and sorts as a column
    he is absent from rather than as the bottom of it.
    """
    place = head.get("cell")
    if place is None:
        value = row[head["field"]]
        return value.casefold() if head["type"] == "text" else value
    cell = row["cells"][place]
    return None if cell["blank"] else cell["pace"]


def _ordered(rows: list[dict], head: dict, way: str) -> list[dict]:
    """The rows the way one column is read: highest first for a figure, A to Z for a name.

    A player with no figure in the column sorts past every player who has one, whichever way the
    column is read, and players level on it are read by name so that two boards of the same
    league draw the same rows in the same order. No row is dropped for having no figure: the
    column keeps every player it is offered, the ones without it at its far end.
    """
    descending = way == "desc"
    if head["type"] == "text":
        return sorted(rows, key=lambda row: _value(row, head), reverse=descending)

    def figure(row: dict) -> tuple[bool, float, str]:
        value = _value(row, head)
        return (True, 0.0, row["name"].casefold()) if value is None else (
            False, -value if descending else value, row["name"].casefold())

    return sorted(rows, key=figure)


def _chosen(heads: list[dict], sort: str, direction: str) -> tuple[dict, str]:
    """The column this board is read by and the way round, resolved from what an address says.

    An address that names no column, or names one this board does not draw, is answered with the
    board's own opening -- its first stat column, highest first. A column named without a way
    round, or with a word that is neither, is read the way that kind of column is read first:
    highest first for a figure, A to Z for a name.
    """
    opening = next((head for head in heads if "cell" in head), heads[0])
    wanted = str(sort or "").strip().lower()
    head = next((item for item in heads if item["key"] == wanted), opening)
    way = str(direction or "").strip().lower()
    if way not in WAYS:
        way = "desc" if head["type"] == "number" else "asc"
    return head, way


def _unreadable(season: str, note: str) -> dict:
    """A board with nothing to project, in the lookup's own words rather than as an empty table."""
    return {
        "season": str(season or ""),
        "season_games": SEASON_GAMES,
        "weeks": 0, "weeks_read": 0, "unread": [],
        "position": "", "chips": [], "columns": [], "rows": [], "heads": [],
        "players_total": 0, "example": [],
        "sort": "", "direction": "", "saying": "", "opening": True,
        "open_sort": "", "open_direction": "desc",
        "note": note,
    }


def get_pace_board(league_id: str, season: str = "", position: str = "",
                   sort: str = "", direction: str = "") -> dict:
    """Build the Pace board for one season, narrowed and ordered as an address asks.

    ``season`` names the season to project -- empty is the one being played -- and ``position``
    is the chip chosen, empty for the whole league. ``sort`` names the column to read the board
    by and ``direction`` which way round, both as a pasted address carries them; either may be
    missing, and the board then opens on points. A season with nothing to project comes back as
    a board that says so in the lookup's own words, rather than as an empty table or an error: a
    league whose first week has not been played is a league the page can still describe.

    A board narrowed to one position carries the stats that position is read beside as well as
    the ones it is measured by, faded and sorted like any other column; a board of every position
    carries only the measured ones, so a column that is a faded carry count on one board is not a
    column at all on the other.
    """
    try:
        board = pace_lines(league_id, season)
    except SleeperError as exc:
        return _unreadable(season, str(exc))
    wanted = str(position or "").strip().upper()
    wanted_players = [who for who in board["players"]
                      if not wanted or who["position"].upper() == wanted]

    # The columns are the stats the shown players between them carry, in the order a pace reads:
    # points first, because that is chiefly what a pace is, then points a game, then the stats a
    # position is measured by. A column nobody here has is left out rather than drawn empty, and
    # a player whose position does not carry a column gets a dash in it.
    labels = stat_table()
    order = ["PTS", "PPG"] + [abbr for abbr in stat_order() if abbr not in ("PTS", "PPG")]
    carried = [abbr for abbr in order
               if any(abbr in {row["abbr"] for row in who["line"]} for who in wanted_players)]
    columns = [{"abbr": abbr, "label": labels[abbr]["label"]} for abbr in carried]
    # A board narrowed to one position carries the work behind that position's figures as well,
    # each faded column to the left of the stat it explains. Every other board leaves them out: a
    # board of every position is read for what the league scores, and a column of carries there
    # would be a carry count for backs and a dash for everybody else.
    if wanted:
        columns = _with_context(columns, context_stats(wanted))
    # A board narrowed to one position is one position all the way down, so the column that would
    # print the same two letters in every row is dropped and the table is headed with the position
    # itself instead. It is dropped here rather than hidden in the page because the headings are
    # what the browser sorts by: a column that is not drawn is not a column to order by, and the
    # remaining cells shift up with it.
    heads = [head for head in _heads(columns) if not (wanted and head["key"] == "pos")]

    rows: list[dict] = []
    for who in wanted_players:
        figures = {row["abbr"]: row for row in who["line"]}
        # The faded columns are the same season as the line beside them: their figures are
        # projected here from the totals the line was projected from, on the same games, so a back
        # reads his carries and his rushing yards as two readings of one season rather than two
        # seasons that happen to agree.
        beside = ({row["abbr"]: row for row in context_rows(wanted, who["totals"], who["games"])}
                  if wanted else {})
        # One entry per column, in the columns' own order, so a row and the header above it are
        # read from the same list rather than from two that have to be kept in step.
        cells = []
        for column in columns:
            line = figures.get(column["abbr"]) or beside.get(column["abbr"])
            cells.append({
                # A dash, not a zero: this player's position is not measured by this stat, so
                # there is no figure to print and the table says so where the figure would be.
                "figure": pace_figure(line["pace"], line["decimals"]) if line else "",
                "pace": line["pace"] if line else None,
                "blank": line is None,
            })
        rows.append({
            "id": who["id"],
            "name": who["name"],
            "position": who["position"],
            "games": who["games"],
            "cells": cells,
        })

    # The column the board is read by, and the way round: asked for by the address, and answered
    # with the board's own opening where the address does not name one of this board's columns.
    # The rows are ordered here rather than in the browser, so a board pasted into a chat arrives
    # already in the order it was shared in, and reads the same before any script has run.
    head, way = _chosen(heads, sort, direction)
    if rows:
        rows = _ordered(rows, head, way)
    opening_head, opening_way = _chosen(heads, "", "")
    opening = head["key"] == opening_head["key"] and way == opening_way
    for item in heads:
        here = item["key"] == head["key"]
        item["aria"] = ("descending" if way == "desc" else "ascending") if here else "none"
        item["arrow"] = ("↓" if way == "desc" else "↑") if here else "↕"

    counts = Counter(who["position"] for who in board["players"])
    places = [place for place in NAMED_POSITIONS if counts.get(place)]
    places += sorted(place for place in counts if place not in NAMED_POSITIONS)
    chips = [{"code": "", "label": "All", "count": len(board["players"])}]
    chips += [{"code": place, "label": place, "count": counts[place]} for place in places]

    note = ""
    if not rows:
        if wanted:
            note = (f"No {wanted} this league has scored in {board['season']} has a game to "
                    "project from.")
        else:
            note = (f"No player this league has scored in {board['season']} has a game to "
                    "project from yet.")

    return {
        "season": board["season"],
        "season_games": board["season_games"],
        "weeks": board["weeks"],
        "weeks_read": board["weeks_read"],
        "unread": board["unread"],
        "position": wanted,
        "chips": chips,
        "columns": columns,
        "heads": heads,
        "rows": rows,
        "players_total": len(board["players"]),
        # The comparison the panel opens on, for a reader who has not asked for one: the two backs
        # at the top of this same read, whose figures the page then compares like any other pair.
        "example": _example_players(board["players"]),
        # What an address says to open this same board again: the column and the way round, the
        # words the sentence above the table names it by, and whether this is the board's own
        # opening -- which a link leaves out of itself, because the opening is what a page with
        # no words about it already does. The opening is handed over as well, and not only the
        # fact that this board is it: the browser needs to know which order to leave unsaid when
        # it writes a sorted board back into the address.
        "sort": head["key"],
        "direction": way,
        "saying": head["saying"] if rows else "",
        "opening": opening,
        "open_sort": opening_head["key"],
        "open_direction": opening_way,
        "note": note,
    }
