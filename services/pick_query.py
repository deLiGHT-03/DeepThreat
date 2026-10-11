"""Reading a draft pick from what someone typed into the search box.

The trades search box takes a player's name, and a pick is the other thing a manager searches
for -- "2025 1st", "2027 2nd", "the 2.02". This turns that text into a pick, and answers nothing
at all when the text is not one, so a name never has to be told apart from a pick by the caller:
:func:`read` returning None *is* the statement that the text is a name.

What a typed pick can and cannot say is worth being exact about, because the data is:

* A **round and a season** is what Sleeper records about a traded pick -- that, and the roster it
  started with. "2025 1st" is therefore answerable exactly.
* A **season on its own** names every pick of that draft: "2024" is read as all the 2024 picks
  that changed hands, in every round, which is what a year typed into the box means. A round on
  its own ("1st") is the same the other way round -- every first-round pick, any season.
* A **slot** ("2.02", the second pick of the second round) names a place in a draft, and the
  draft says who used that place and who was taken. It does not say which of a team's traded
  picks it was: a team holding three second-round picks used three places with them and Sleeper
  never paired one with the other. The slot is carried through so a caller can show what that
  place became, but it is never allowed to claim a trade it cannot prove.
"""

from __future__ import annotations

import re

#: The rounds, as they are written: "1st", "1", "first". The digits and words map to the number.
ROUND_WORDS = {
    "1": 1, "1st": 1, "first": 1,
    "2": 2, "2nd": 2, "second": 2,
    "3": 3, "3rd": 3, "third": 3,
    "4": 4, "4th": 4, "fourth": 4,
    "5": 5, "5th": 5, "fifth": 5,
    "6": 6, "6th": 6, "sixth": 6,
    "7": 7, "7th": 7, "seventh": 7,
    "8": 8, "8th": 8, "eighth": 8,
    "9": 9, "9th": 9, "ninth": 9,
    "10": 10, "10th": 10, "tenth": 10,
}

#: Words that carry no information in a pick search, so they are read past.
NOISE = frozenset({"round", "rounds", "rd", "rds", "pick", "picks", "draft", "drafts",
                   "season", "seasons", "the", "a", "an", "in", "of", "for", "my", "our",
                   "and"})

#: "2.02" or "2-02": a round and the pick within it.
SLOT = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})$")

#: A season, as Sleeper writes it.
YEAR = re.compile(r"^(19|20)\d{2}$")


def _round_of(token: str) -> int | None:
    return ROUND_WORDS.get(token)


def read(text: str) -> dict | None:
    """Read a pick out of ``text``, or return None when it is not one.

    Everything typed has to be understood for the text to be a pick: a season, a round, a place
    in a round, or a word that means nothing in a pick. One word that fits none of those makes
    the whole thing a name, which is what stops a search for "Josh Palmer" being read as a pick
    just because it contains a letter or a number.

    A season or a round on its own is a pick -- "2024" asks for every 2024 pick, "1st" for every
    first-round one -- so the only text that is not a pick is text with neither. That is why
    :func:`read` returning None is exactly the statement that the text is a name.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    cleaned = raw.casefold()
    for joiner in ("#", ",", "'"):
        cleaned = cleaned.replace(joiner, " ")
    tokens = [token for token in re.split(r"\s+", cleaned) if token]
    season = ""
    round_number: int | None = None
    slot: int | None = None
    for token in tokens:
        token = token.strip(".")
        if not token:
            continue
        if token in NOISE:
            continue
        if YEAR.match(token):
            if season and season != token:
                return None  # two seasons in one search is not a pick.
            season = token
            continue
        placed = SLOT.match(token)
        if placed:
            if round_number is not None:
                return None
            round_number, slot = int(placed.group(1)), int(placed.group(2))
            continue
        number = _round_of(token)
        if number is not None:
            if round_number is not None and round_number != number:
                return None  # two different rounds in one search is not a pick.
            round_number = number
            continue
        return None  # A word that is not part of a pick: this is a name.
    if round_number is None and not season:
        return None  # Neither a season nor a round: this is a name.
    if slot is not None and not 1 <= slot <= 32:
        return None
    if round_number is not None and not 1 <= round_number <= 30:
        return None
    return {"season": season, "round": round_number, "slot": slot, "text": raw}


def label(spec: dict) -> str:
    """Name a pick the way a manager says it: "2025 2nd Rd", "2nd Rd", or "2024 draft pick".

    A season with no round is every pick of that draft, so it is named as the draft rather than
    as a round of it.
    """
    from services.trades import round_label

    season = str(spec.get("season") or "")
    if spec.get("round") is None:
        return f"{season} draft pick".strip() if season else "draft pick"
    round_name = round_label(spec["round"])
    if spec.get("slot"):
        round_name = f"{round_name} · pick {spec['slot']:02d}"
    return f"{season} {round_name}".strip() if season else round_name


def _ordinal(number: int) -> str:
    """Name a round as an adjective: 1st, 2nd, 3rd, 4th."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def describe(spec: dict) -> str:
    """Say what a pick search will actually match, so the page promises no more than it can."""
    season = str(spec.get("season") or "")
    where = f"the {season} draft" if season else "every season this league has drafted"
    if spec.get("round") is None:
        return (f"every pick of {where} that changed hands, in every round"
                if season else "every draft pick that changed hands, in every round")
    if spec.get("slot"):
        return (f"{_ordinal(spec['round'])}-round picks in {where} — the place "
                f"{spec['round']}.{spec['slot']:02d} names who used it and who was taken, "
                f"while the trades below are the ones that moved a pick of that round")
    return f"{_ordinal(spec['round'])}-round picks in {where}"
