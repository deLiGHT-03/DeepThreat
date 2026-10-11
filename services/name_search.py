"""Loose name matching, for a search box a reader types into from memory.

A search has to answer to a name spelled from memory: without its accents, sometimes without
its punctuation, now and then with a letter in the wrong place, and often as nothing but a
surname. Names are therefore compared word by word -- folded to plain lowercase letters and
digits -- and a typed word counts as one of the name's own when it is typed in full, when it
is how that word starts, when it sits inside it, or when it is a mistake or two away from it.
A pair of swapped letters counts as one mistake, because that is the one a hurried typist
makes ("Chaes" for "Chase").

Every word the reader typed has to land somewhere in the name, in any order, so a surname
typed first ("jefferson justin") finds the same player as the name typed in order, and a
stray word does not half-match somebody ("chase smith" is not Ja'Marr Chase).

Scores are graded rather than pass or fail, so a caller can rank several players, and a
search that settled on nobody can still be told which names it came closest to.
"""

from __future__ import annotations

import re
import unicodedata

#: The endings a name reads past, so "Marvin Harrison Jr." answers a search for "harrison".
NAME_ENDINGS = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

#: How sure a match is, by the way it matched. Weights rather than probabilities: a word typed
#: in full beats a word it starts, which beats a word it sits inside, which beats one it is a
#: letter or two away from.
MATCH_EXACT = 1.0
MATCH_PREFIX = 0.95
MATCH_INSIDE = 0.9
MATCH_TYPO = 0.85

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    """Break a name into the words a search compares: no accents, no case, no punctuation.

    An apostrophe or a full stop inside a name joins it rather than breaking it -- "D'Andre",
    "T.J." and "Ja'Marr" are words a reader types whole -- while a hyphen is a break, and the
    squashed form :func:`affinities` also tries reads past that one.
    """
    folded = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    for joiner in ("'", "\u2019", "."):
        plain = plain.replace(joiner, "")
    parts = _WORD.findall(plain.casefold())
    if len(parts) > 1 and parts[-1] in NAME_ENDINGS:
        parts = parts[:-1]
    return parts


def typo_allowance(length: int) -> int:
    """How many mistyped letters a word of this length is allowed.

    A short word has to be right -- nobody searches for "ra" and means something else -- while
    a long surname survives two slips, which is what a name typed from memory needs. Letting
    a short word slide is how a search stops being about the player who was asked for.
    """
    if length >= 7:
        return 2
    if length >= 4:
        return 1
    return 0


def _edit_distance(left: str, right: str) -> int:
    """How far apart two words are, with a pair of swapped letters counting as one mistake.

    Optimal string alignment: plain edit distance plus the transposition, which is the typo a
    name search actually meets.
    """
    if left == right:
        return 0
    two_rows_back: list[int] = []
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row] + [0] * len(right)
        for column, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current[column] = min(previous[column] + 1, current[column - 1] + 1,
                                  previous[column - 1] + cost)
            if (row > 1 and column > 1 and left_char == right[column - 2]
                    and left[row - 2] == right_char):
                current[column] = min(current[column], two_rows_back[column - 2] + 1)
        two_rows_back, previous = previous, current
    return previous[-1]


def word_affinity(typed: str, word: str) -> float:
    """How sure a search is that ``word`` is the word being typed, from 0 (no) to 1 (yes)."""
    if typed == word:
        return MATCH_EXACT
    if word.startswith(typed):
        return MATCH_PREFIX          # "chas" is how "Chase" starts
    if len(typed) >= 4 and typed in word:
        return MATCH_INSIDE          # "hase" is inside "Chase"
    allowed = max(typo_allowance(len(typed)), typo_allowance(len(word)))
    if allowed and _edit_distance(typed, word) <= allowed:
        return MATCH_TYPO            # a letter or two in the wrong place
    return 0.0


def affinities(query: str, name: str) -> list[float]:
    """How well each word typed lands on the name, word for word.

    The name is also compared as one squashed word, because a name carrying a space or a
    hyphen is often typed without one -- "amonra" for "Amon-Ra St. Brown".
    """
    typed, name_words = words(query), words(name)
    if not typed or not name_words:
        return []
    squashed = "".join(name_words)
    return [max([word_affinity(word, candidate) for candidate in name_words]
                + [word_affinity(word, squashed)]) for word in typed]


def score(query: str, name: str) -> float:
    """How well one name answers a search: 1 for the name itself, 0 for a name that is not it.

    Every word typed has to land on the name for this to count at all.
    """
    found = affinities(query, name)
    if not found or min(found) <= 0:
        return 0.0
    return sum(found) / len(found)


def closeness(query: str, name: str) -> float:
    """The same measure with the gate left open: what a near miss looks like."""
    found = affinities(query, name)
    return sum(found) / len(found) if found else 0.0


def match(query: str, players: list[dict], suggestions: int = 3) -> tuple[list[dict], list[str]]:
    """Split candidate players into the ones a search means and the names nearest to it.

    Players come back best first, and only when the search settled on somebody. When it
    settled on nobody, the closest names come back instead, so a page can say what it nearly
    found rather than only that it found nothing.
    """
    scored = [(score(query, player["name"]), player) for player in players]
    found = sorted((pair for pair in scored if pair[0] > 0),
                   key=lambda pair: (-pair[0], pair[1]["name"].casefold()))
    if found:
        return [player for _, player in found], []
    nearest = sorted(((closeness(query, player["name"]), player["name"]) for player in players),
                     key=lambda pair: (-pair[0], pair[1].casefold()))
    return [], [name for value, name in nearest[:suggestions] if value > 0]
