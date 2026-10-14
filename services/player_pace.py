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
    # caught it, so his rushing attempts are not a column here: scrimmage yards is his rushing
    # and receiving yards added up and projected from the season's totals, rather than a reader's
    # sum of the two rounded figures printed beside it.
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

#: Every stat key the tables above add up, gathered once so a week's line is walked once.
_STAT_KEYS = tuple(sorted({key for stats in POSITION_STATS.values()
                           for _, _, keys, _ in stats for key in keys}))

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

    This is the only place a pace is worked out. ``totals`` is his own summed stat keys and
    ``points`` the league's own scoring for the same games, so the caller decides what a game is
    and this decides only how a season's figure projects over a full one -- which is what makes a
    row of a ranking and the row under the same player's card the same figure, on the same games,
    rather than two calculations that happen to agree today.

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


def get_player_pace(league_id: str, player_id: str) -> dict:
    """Build one player's 17-game pace: the stats his position carries, and his points.

    Only a position the table above knows has a pace -- a kicker and a defense are not measured
    by these stats, and quoting them as though they were would be inventing a figure rather than
    projecting one.
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

    rows = pace_rows(position, totals, scored, games)

    name, _ = _player_display_names(player, player_id)
    return {
        "id": player_id,
        "name": name,
        "position": position,
        "season": season,
        "games": games,
        "season_games": SEASON_GAMES,
        "rows": rows,
    }
