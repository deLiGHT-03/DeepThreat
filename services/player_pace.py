"""One player's 17-game pace, for the button at the foot of his card.

A card says what a player has already done; this says what he is on course to do. For each stat
his position is judged by, the season's figure is averaged over the games he actually played and
projected over a full 17-game season, so a player who has missed time can be read beside one who
has not.

Two sources, each used for the only thing it can be trusted with. The counting stats -- passing,
rushing and receiving yards, receptions, touchdowns and turnovers -- come from Sleeper's own game
log for the player, which is one request and carries his official stat line for every week he
played. The points come from this league's own weekly records, the same figure his card totals, so
a pace here is on the same scale as the numbers around the card rather than on Sleeper's default
scoring.

The weeks counted are the card's weeks: a week the league has scored and that Sleeper recorded him
taking the field in. A bye, an inactive week and a week still to be played are none of them a game
here, and a week he was not on a roster for is not part of his season in this league.
"""

from __future__ import annotations

from services.matchups import _number, _player_display_names, _position
from services.player_card import _points_by_player
from services.sleeper import (SleeperError, get_league_dashboard, get_player_index,
                              get_player_season_games, season_week_count)

#: The season a pace projects over. An NFL season is seventeen games, and a pace is read as
#: "over a full season", so this is the number a manager already has in his head.
SEASON_GAMES = 17

#: Which stats each position is measured by, in the order the table reads them. Each is a label,
#: the abbreviation the table prints, the stat keys it adds up (one for most, several wherever the
#: stat is not a column of Sleeper's own line -- a touchdown, a turnover, or scrimmage yards, a
#: back's rushing and receiving yards added up), and how many decimals it is printed to: yards,
#: catches and scores are whole numbers, points are not.
POSITION_STATS: dict[str, tuple[tuple[str, str, tuple[str, ...], int], ...]] = {
    "QB": (
        ("Passing Yards", "PASY", ("pass_yd",), 0),
        ("Total Touchdowns", "TTD", ("pass_td", "rush_td", "rec_td"), 0),
        ("Turnovers", "TOT", ("pass_int", "fum_lost"), 0),
        ("Rushing Yards", "RUSY", ("rush_yd",), 0),
    ),
    # A back is read by what he does with the ball in his hands, whether he was handed it or
    # caught it, so his rushing attempts are not a row of his pace line: scrimmage yards is his
    # rushing and receiving yards added up and projected from the season's totals, rather than a
    # reader's sum of the two rounded figures printed beside it. The attempts are not gone from
    # the board he is read on, though -- they and his targets are drawn there beside the stat
    # they were earned in, which is what :data:`CONTEXT_STATS` below is.
    "RB": (
        ("Rushing Yards", "RUSY", ("rush_yd",), 0),
        ("Receptions", "REC", ("rec",), 0),
        ("Receiving Yards", "RECY", ("rec_yd",), 0),
        ("Scrimmage Yards", "SCRIM", ("rush_yd", "rec_yd"), 0),
        ("Total Touchdowns", "TTD", ("pass_td", "rush_td", "rec_td"), 0),
    ),
    "WR": (
        ("Receptions", "REC", ("rec",), 0),
        ("Receiving Yards", "RECY", ("rec_yd",), 0),
        ("Total Touchdowns", "TTD", ("pass_td", "rush_td", "rec_td"), 0),
    ),
    "TE": (
        ("Receptions", "REC", ("rec",), 0),
        ("Receiving Yards", "RECY", ("rec_yd",), 0),
        ("Total Touchdowns", "TTD", ("pass_td", "rush_td", "rec_td"), 0),
    ),
}

#: The stats a position is read *beside* on the Pace board rather than measured by: the work a
#: figure was earned in, which is not a figure the league scores. Each is the same four parts as a
#: stat of :data:`POSITION_STATS` -- a label, the abbreviation a heading prints, the Sleeper keys
#: it adds up and how many decimals it is printed to -- and then the abbreviation of the stat it
#: is read to the left of, which is where a board puts it.
#:
#: They are the board's own and nobody else's. A pace line is the stats a position is measured by,
#: so these are not rows of one: a card's pace table and an Ask AI ranking are read without them,
#: and a board of every position at once leaves them out too -- twelve columns of what a league
#: scores are enough to read a whole league by, and the carries and targets behind them are wanted
#: when a reader is looking at one position rather than all of them.
#:
#: Every position that catches the ball carries targets, the tight end included: a target is the
#: attempt a catch was made of whichever of them it was thrown to. Only a back is read beside his
#: carries, because he is the one the league hands the ball to.
CONTEXT_STATS: dict[str, tuple[tuple[str, str, tuple[str, ...], int, str], ...]] = {
    "RB": (
        ("Rush Attempts", "RATT", ("rush_att",), 0, "RUSY"),
        ("Targets", "TAR", ("rec_tgt",), 0, "REC"),
    ),
    "WR": (
        ("Targets", "TAR", ("rec_tgt",), 0, "REC"),
    ),
    "TE": (
        ("Targets", "TAR", ("rec_tgt",), 0, "REC"),
    ),
}

#: Every stat key the tables above add up, gathered once so a week's line is walked once. The
#: context stats are summed with the rest of them: they are not rows of a pace line, but the board
#: draws them out of the same totals a line is projected from, so their keys have to be in hand.
_STAT_KEYS = tuple(sorted(
    {key for stats in (*POSITION_STATS.values(), *CONTEXT_STATS.values())
     for stat in stats for key in stat[2]}))

#: The two figures every position carries. They are scored by the league rather than read from a
#: key in Sleeper's line, which is why they are kept out of :data:`POSITION_STATS`: that table is
#: what one position is measured by, and these are what all of them are.
SCORED_STATS = (("PTS", "Points", 1), ("PPG", "Points Per Game", 1))


def stat_table() -> dict[str, dict]:
    """Every stat a pace can be read from, and the positions that are measured by it.

    Built from :data:`POSITION_STATS` rather than written out a second time, so a stat is
    rankable exactly when some position's pace table holds it -- the two can never drift apart,
    and a stat added to a position is one a ranking can immediately be asked for.

    Each entry carries the label and the abbreviation the table prints, the Sleeper keys the stat
    adds up, how many decimals it is printed to, and which positions carry it. ``positions`` is
    None for points and points a game, which is every position rather than a set of them: the
    league scores whoever it likes. ``scored`` says the figure is the league's own rather than a
    sum of Sleeper's keys, and ``rate`` says it is already a per-game number, so a full season
    cannot change it.
    """
    table: dict[str, dict] = {}
    for place, stats in POSITION_STATS.items():
        for label, abbreviation, keys, decimals in stats:
            entry = table.setdefault(abbreviation, {
                "label": label, "abbr": abbreviation, "keys": keys, "decimals": decimals,
                "positions": set(), "rate": False, "scored": False,
            })
            entry["positions"].add(place)
    for abbreviation, label, decimals in SCORED_STATS:
        table[abbreviation] = {
            "label": label, "abbr": abbreviation, "keys": (), "decimals": decimals,
            "positions": None, "rate": abbreviation == "PPG", "scored": True,
        }
    return table


def pace_figure(value: float, decimals: int) -> str:
    """A pace figure the way a player card prints it: whole numbers grouped, decimals as they are."""
    return f"{float(value):,.{int(decimals)}f}"


def stat_order() -> tuple[str, ...]:
    """Every stat a pace line can hold, in the order a table reads them.

    Built from :data:`POSITION_STATS` for the same reason :func:`stat_table` is: a stat's place
    in a table is the place its position gives it, so the columns of a ranking and the rows of
    one player's pace are one order rather than two that can drift apart. The two scored stats
    come last, because they are every position's rather than one position's.
    """
    order: list[str] = []
    for stats in POSITION_STATS.values():
        for _label, abbreviation, _keys, _decimals in stats:
            if abbreviation not in order:
                order.append(abbreviation)
    order.extend(abbreviation for abbreviation, _label, _decimals in SCORED_STATS)
    return tuple(order)


def _pace(total: float, games: int) -> float:
    """One season figure, projected over a full season at the rate it was earned at."""
    return total / games * SEASON_GAMES


def pace_rows(position: str, totals: dict[str, float], points: float, games: int) -> list[dict]:
    """One player's pace line: the stats his position is measured by, then his points.

    This is the only place a pace line is worked out -- :func:`context_rows` projects the board's
    own faded columns from the same totals by the same arithmetic -- and ``totals`` is his own
    summed stat keys and ``points`` the league's own scoring for the same games, so the caller
    decides what a game is and this decides only how a season's figure projects over a full one --
    which is what makes a row of a ranking and the row under the same player's card the same
    figure, on the same games, rather than two calculations that happen to agree today.

    A position with no table of its own -- a kicker, a defense -- gets the two scored stats and
    nothing else, because the league scores whoever it likes and measures only some of them by
    the rest.
    """
    if games < 1:
        return []
    rows = [{
        "label": label,
        "abbr": abbreviation,
        "pace": round(_pace(sum(totals.get(key, 0.0) for key in keys), games), decimals),
        "decimals": decimals,
    } for label, abbreviation, keys, decimals in POSITION_STATS.get(position or "", ())]
    rows.append({
        "label": "Points",
        "abbr": "PTS",
        "pace": round(_pace(points, games), 1),
        "decimals": 1,
    })
    # Points per game is already a rate, so a full season does not change it: the pace of a
    # per-game figure is that figure. It is kept as a row of its own because it is how two
    # players are actually compared.
    rows.append({
        "label": "Points Per Game",
        "abbr": "PPG",
        "pace": round(points / games, 1),
        "decimals": 1,
        "rate": True,
    })
    return rows


def context_stats(position: str) -> list[dict]:
    """The stats a position is read beside on the Pace board, in the order that board draws them.

    Each is the label and abbreviation a heading prints, the keys its figure is summed from, how
    many decimals it is printed to, and ``before``: the abbreviation of the stat it stands to the
    left of. That last one is how the board knows where in its own columns to put one, rather
    than keeping a second list of the columns' order somewhere else that could drift from this.
    """
    return [{
        "label": label, "abbr": abbreviation, "keys": keys, "decimals": decimals,
        "before": before,
    } for label, abbreviation, keys, decimals, before in CONTEXT_STATS.get(position or "", ())]


def context_rows(position: str, totals: dict[str, float], games: int) -> list[dict]:
    """One player's context figures: the work behind the stats his position is measured by.

    Read from the same ``totals`` and the same ``games`` as his pace line, and projected by the
    same arithmetic, because the two are summed out of one week's stats in the first place: a
    column of carries beside a column of rushing yards is the same season read twice rather than
    two readings of it that have to be kept in step.
    """
    if games < 1:
        return []
    return [{
        "label": stat["label"],
        "abbr": stat["abbr"],
        "pace": round(_pace(sum(totals.get(key, 0.0) for key in stat["keys"]), games),
                      stat["decimals"]),
        "decimals": stat["decimals"],
        # Where the stat it belongs to is, carried on the row itself: a reader of these puts the
        # figure beside that stat, and neither the board nor a card keeps a second list of where
        # each one goes.
        "before": stat["before"],
    } for stat in context_stats(position)]


def beside_note(label: str, beside: str) -> str:
    """What a faded figure says about itself: the stat it is read beside, in one place.

    A heading on the Pace board and a row on a card both say it, and a reader who meets the same
    grey figure in both places should not have to work out whether it means the same thing.
    """
    return f"{label}, read beside {beside}"


def with_context(rows: list[dict], position: str, totals: dict[str, float],
                 games: int) -> list[dict]:
    """A pace line with the work behind each figure read with it, marked and titled as faded.

    This is the card's reading of what the Pace board says in a column: a card has no columns to
    put a faded figure beside anything, so the row goes directly **above** the stat it was earned
    in, which is where the eye meets it before the figure it explains. Each one is marked
    ``context`` so a card can set it faded, and carries the note a heading on the board carries,
    so the grey reads the same on both. A context stat whose stat is not in these rows -- one that
    could never be drawn -- is left off rather than hung under nothing.
    """
    beside = context_rows(position, totals, games)
    if not beside:
        return rows
    placed: list[dict] = []
    for row in rows:
        for stat in beside:
            if stat["before"] == row["abbr"]:
                placed.append({**stat, "context": True,
                               "title": beside_note(stat["label"], row["label"])})
        placed.append(row)
    return placed


def _read_season(league_id: str, player_id: str) -> dict:
    """A player's season as a pace is read from it: who he is, what he did, and over how many games.

    The one read behind a pace, whoever is asking for one -- a card's line and the same line with
    the work behind it are two readings of this rather than two reads of the season -- so the
    weeks counted, the games and the totals cannot differ between two callers that both project
    from it. Raises :class:`~services.sleeper.SleeperError` for a player who cannot be projected:
    one not in Sleeper's list, a position with no table of its own, or a season with no game of
    his in it.
    """
    dashboard = get_league_dashboard(league_id)
    league = dashboard["league"]
    player_id = str(player_id).strip()
    player = get_player_index().get(player_id)
    if not player:
        raise SleeperError("That player is not in Sleeper's player list.")
    position = _position(player) or ""
    stats = POSITION_STATS.get(position)
    if not stats:
        raise SleeperError(f"A {position or 'player'} has no pace table.")

    season = str(league.get("season") or "")
    # The same window his card counts, so the games behind a pace here are the games behind the
    # total printed on the card it was opened from.
    weeks = season_week_count(league)
    try:
        log = get_player_season_games(player_id, season)
    except SleeperError:
        log = {}
    points = _points_by_player(league_id, weeks)
    scored_weeks = points.get(player_id, {}) if isinstance(points, dict) else {}

    lines: list[dict] = []
    counted: list[int] = []
    for week in range(1, weeks + 1):
        game = log.get(str(week))
        if not isinstance(game, dict):
            continue
        line = game.get("stats")
        # ``gp`` is what says he took the field: a bye and an inactive week are both null here,
        # and a week with no recorded score is one the league never counted him in.
        if not isinstance(line, dict) or not (_number(line.get("gp")) or 0.0) > 0:
            continue
        if week not in scored_weeks:
            continue
        lines.append(line)
        counted.append(week)

    games = len(lines)
    if not games:
        raise SleeperError(f"No games to project for {_player_display_names(player, player_id)[0]} yet.")

    totals = {key: 0.0 for key in _STAT_KEYS}
    for line in lines:
        for key in _STAT_KEYS:
            value = _number(line.get(key))
            if value is not None:
                totals[key] += value

    # The points come from the league's own weekly records over the same games the stat lines
    # above are read from, so a pace's points and its stats describe one season, not two.
    scored = sum(_number(scored_weeks.get(week)) or 0.0 for week in counted)

    name, _ = _player_display_names(player, player_id)
    return {
        "id": player_id,
        "name": name,
        "position": position,
        "season": season,
        "games": games,
        "totals": totals,
        "scored": scored,
    }


def _pace_answer(read: dict, rows: list[dict]) -> dict:
    """One player's pace, with the rows whoever asked for it worked out."""
    return {
        "id": read["id"],
        "name": read["name"],
        "position": read["position"],
        "season": read["season"],
        "games": read["games"],
        "season_games": SEASON_GAMES,
        "rows": rows,
    }


def get_player_pace(league_id: str, player_id: str) -> dict:
    """Build one player's 17-game pace: the stats his position carries, and his points.

    The scored line and nothing else, which is what a ranking, an Ask AI answer about a player and
    a comparison are all read from. A card draws the same line with the work behind a figure on it
    instead -- see :func:`get_card_pace`, which is this read and those rows rather than a second
    projection.

    Only a position the table above knows has a pace -- a kicker and a defense are not measured
    by these stats, and quoting them as though they were would be inventing a figure rather than
    projecting one.
    """
    read = _read_season(league_id, player_id)
    return _pace_answer(read, pace_rows(read["position"], read["totals"], read["scored"],
                                        read["games"]))


def get_card_pace(league_id: str, player_id: str) -> dict:
    """One player's pace as his card draws it: his line, with the work behind a figure on it.

    The same rows :func:`get_player_pace` returns, with a faded row above each stat that has work
    behind it -- a back's carries above his rushing yards, his targets above his catches, and the
    same for a receiver or a tight end. It is the reading the Pace board gives the same figures in
    a column to the left of them, and it is deliberately the card's own: a caller that ranks
    players, compares two of them or answers a question about one reads the scored line, where a
    carry count has no business being ranked beside a yardage figure.
    """
    read = _read_season(league_id, player_id)
    rows = with_context(pace_rows(read["position"], read["totals"], read["scored"], read["games"]),
                        read["position"], read["totals"], read["games"])
    return _pace_answer(read, rows)
