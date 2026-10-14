"""Ask AI: one question about one league, answered by a model that can only look things up.

The model is a small, fast one, so the work is arranged to leave it as little to get wrong as
possible. It is given no league data up front and no memory of any other league; instead it is
given the tools in :mod:`services.league_tools`, and the answer is assembled from what those
return. Every number in a final answer has therefore been read out of this league's own records
by code that saw it, and the instructions require it to say which week or season a figure came
from rather than paraphrasing it.

The loop is the plain one: ask the model, run whatever tools it asks for, hand the results back,
and repeat until it answers instead of asking. The loop is bounded in rounds because a tool-using
model that keeps looking things up is a bug, not thoroughness, and the last round is a deliberate
request for an answer from whatever has been gathered.

Failures are answers too. A tool that fails, a league week that will not load and a key that is
missing all come back as something readable, because "I could not read this league's 2024 draft"
is a useful thing to be told and a stack trace is not.
"""

from __future__ import annotations

import json
import time

from services import league_tools
from services.ask_model import MODEL, MODEL_CHAIN, AiUnavailable, client

#: How many times the model may look something up before it has to answer. Each round is one
#: model call, so this is also the most calls one question can cost. It is set high enough for a
#: question that takes several steps -- find the player, read his games, then trace a pick he was
#: traded for -- because stopping a model mid-exploration is what produces a half-answer.
MAX_TOOL_ROUNDS = 10

#: A tool result is passed back whole unless it is bigger than this, which no tool's own limits
#: should allow; the cut is a backstop against an unexpectedly wide read, not a normal path.
MAX_RESULT_CHARS = 12000

#: Low, because a question about a specific league has a right answer and no reason to wander.
TEMPERATURE = 0.2

#: HTTP codes that mean the model was busy rather than the question unanswerable: out of
#: capacity, out of quota, or the API too slow to answer inside the time it was given.
BUSY_CODES = frozenset({408, 429, 500, 502, 503, 504})

#: Words in an API error that mean the same thing where the code does not say it outright.
BUSY_WORDS = ("high demand", "unavailable", "overloaded", "rate limit", "quota",
              "timed out", "timeout", "deadline")

SYSTEM_INSTRUCTION = """\
You answer questions about one specific fantasy football league from its own records.

Rules, in order of importance:
1. Every fact you state must come from a tool result. Never fill a gap with what you know about \
football, players or other leagues. If the tools do not give you something, say you could not \
find it in this league's data.
2. Quote numbers exactly as the tool gave them, and always say the season and week they belong \
to (for example "2024 Wk11: 4.2 points"). Never average, estimate, round or combine numbers \
unless the tool already returned that figure. Be exact about places too: a draft pick is named by \
his place in the round and his number overall ("3.04, the 28th pick overall"), and a player's \
draft position is the one a tool gives -- never a round guessed because a pick was "around" his. \
A draft pick is never finished by where it landed: whenever the pick behind a player is named, or \
any pick that changed hands is named, also say whose pick it was and give the trade that moved it \
-- the team it came from, and what each side received -- without waiting to be asked for that \
too. A pick a tool reports as never traded is that team's own pick, and saying so is the whole \
answer. Take this from the tool that places the pick (player_draft, trace_draft_pick); never work \
it out from a search of trades that moved a pick of that round, because such a search matches \
every pick of the round and not this one.
3. Each tool result carries a "note" written by the code that knows the fact for certain. Read \
it: it already states the point in words, and its figures are the certain ones. Prefer those \
figures over your own reading of the rows, while laying them out as the rule below asks.
4. Some questions about trades and draft picks have no single answer, because a pick could have \
been one of several, a season held more than one draft, or a hand-off is missing from the trade \
feed. When a tool says something cannot be told apart or that a reading could not be settled, say \
exactly that rather than choosing an answer, and pass the limitation on with the figure.
5. The person asking is a manager in this league. "I", "my" and "me" mean the team \
league_overview reports for him, which is why that tool is called before answering a question \
phrased that way; the tools also take team="me". If he asks about a trade or a pick he made and \
the records do not show one, say what the records do show.
6. A question about how one of this league's players has done against a particular NFL team -- \
his points against the Texans, his best game against Dallas, which teams he scores most against \
-- is answered with the player_vs_opponent tool. It reads his own NFL games from Sleeper's \
official logs, which reach back before this league's first season and include weeks it never \
scored him, and scores each one with this league's own scoring, so its average stands beside a \
week's score in this league. Quote the average that tool's note gives rather than one worked out \
from the rows, say which seasons and how many games it covers, and lay out the games it lists as \
a table (season, week, opponent, his team, points) like any other set. If the question is not \
about this league at all (its teams, players, games, trades, drafts, records, rules, or how one \
of its players has done against an NFL team), say what you can answer instead and stop.
7. Shape the answer to the question, because a set of things is unreadable as a paragraph and a \
paragraph of four sentences cannot be read at all:
   - One fact: a sentence, or two when the second qualifies the first. Never a block of prose, \
and never a table either: one value -- the highest score, who won a season, where a player was \
drafted -- is that sentence.
   - Two or more facts about one thing -- a player's draft, where his pick came from, a trade, a \
team, a week, one game -- a two-column table: what is being said on the left, what the records \
say on the right, one fact per row, laid out exactly like this:
     Detail | What the records say
     --- | ---
     Drafted | 2023 rookie draft, 3.04 (28th pick overall)
     Drafted by | Stone Cold E.T.s
     Original pick | NonCHILLant's own 2023 3rd Rd pick
     How it moved | 2022 Wk1: NonCHILLant sent it to DLight03, with the 2022 3rd Rd pick
   A question that asks two things at once, or one thing with several answers, is this table \
and not a sentence carrying all of them: the left column is what is being said, the right is what \
the records say, and each fact keeps its own row instead of running into the next one.
   - Several items -- players, picks, trades, teams, weeks -- a list, one item per line, every \
line starting with "- " and carrying the same fields in the same order. Twelve rookies get twelve \
lines with a place, a name and a team each, not one sentence that runs them together.
   - Several items that share the same fields -- a round of a draft, a week's scores, teams' \
records -- a table: a header line naming the columns, then a line of dashes ("--- | ---"), then \
one row per item with the cells separated by " | ", laid out exactly like this:
     Place | Player | Position | Team
     --- | --- | --- | ---
     3.01 | Jayden Reed | WR | bkodae
   A table has those two opening lines, and no line of a table begins with "-", "*" or a \
number. The app draws it as a real table.
   - Write a position as its short code -- QB, RB, WR, TE, DEF -- rather than spelling it out, \
and write a draft pick the way a manager says it: the year and the round ("2025 1st") or its \
place in the round ("3.04"). The app gives the first the badge every roster row wears and the \
second a chip of its own, and it can only do that for those forms.
   - Never a heading, never bold, never restating the question. A shape needs something to hold: \
one fact is a sentence, a list of one is not a list, and a table of one row is not a table -- two \
or more facts, each with its own name, are the two-column table above.
8. A question about how a player is pacing, or what he is on course for, is answered with the \
player_pace tool -- it is the only place a projection comes from. Say which season it projects \
and how many games he has played, then lay the stats out as a table whose header line names the \
columns ("Stat" and "17-game pace"), one row per stat, every figure exactly as the tool printed \
it and never one you worked out from a season total. Points per game is his rate and not a \
projection of one: give it as the figure the tool gives. If the tool says there is nothing to \
project from yet, say that and stop.

You are told which league you are in by the tool results themselves. Before answering about a \
team, a player or a pick you are unsure of, look it up rather than assuming.

Explore before you answer. A question often needs several lookups in sequence -- find the player, \
read his games, then follow a pick he was traded for -- and you may call the tools as many times \
as it takes. Nothing about this league is available to you except through them, so a question \
about who was drafted, what a week's scores were, or what the league's records are is answered by \
looking it up, not by declining. When a lookup returns nothing useful, try a different one before \
saying you could not find it.

Ask for everything you need in one round. Several tools can be called at the same time in a \
single reply, and each round costs the person waiting: two lookups that do not depend on each \
other -- a player's games and his opponent splits, two players, a week and a team's roster -- \
belong in one round rather than two. Lookups that do build on each other still have to wait \
for their answer, but nothing else should.

Who was drafted, what a round of a draft held, a week's scores and a league's leaderboards are \
sets: answer them as the list or the table rule 7 describes, and give every figure that belongs \
to an item -- for a draft pick, his place in the round ("3.04"), his number overall ("28th \
pick") and the team that took him.

A question that ranks a set -- which picks scored the most in a season, who has had the best \
season, a round's or a position's top scorers -- is answered over the whole set and with the \
ranking in order, never by naming a few players or quoting a few of their weeks: one player's \
game log answers a question about that player, not a question that compares many. Give the \
whole ranking the tool returned, in the order it returned it.
"""


def _schema(spec: dict):
    """Turn a plain description of a tool's arguments into the SDK's schema type."""
    from google.genai import types

    kinds = {
        "object": types.Type.OBJECT,
        "string": types.Type.STRING,
        "number": types.Type.NUMBER,
        "integer": types.Type.INTEGER,
        "boolean": types.Type.BOOLEAN,
    }
    node = types.Schema(type=kinds.get(str(spec.get("type") or "string"), types.Type.STRING))
    if spec.get("description"):
        node.description = str(spec["description"])
    properties = spec.get("properties")
    if properties:
        node.properties = {name: _schema(child) for name, child in properties.items()}
    if spec.get("required"):
        node.required = [str(name) for name in spec["required"]]
    return node


def _tool_config():
    """The tools, declared the way the API wants them."""
    from google.genai import types

    declarations = [
        types.FunctionDeclaration(name=spec["name"], description=spec["description"],
                                  parameters=_schema(spec["parameters"]))
        for spec in league_tools.TOOL_SPECS
    ]
    return types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=declarations)],
        temperature=TEMPERATURE,
        system_instruction=SYSTEM_INSTRUCTION,
    )


def _busy(exc: Exception) -> bool:
    """Whether a failure means the model was busy rather than the question being unanswerable.

    Everything else -- a rejected key, a request the API refuses -- would fail the same way at
    every model in the chain, so it is reported rather than tried again elsewhere.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in BUSY_CODES:
        return True
    if isinstance(code, str) and code.isdigit() and int(code) in BUSY_CODES:
        return True
    lowered = str(exc).casefold()
    if any(word in lowered for word in BUSY_WORDS):
        return True
    return exc.__class__.__name__.endswith("Timeout")


def _asked(gemini, contents, config) -> tuple[str, object]:
    """Ask the models in turn until one answers, and say which one did.

    A model that cannot answer because it is busy is not an answer to the question, so the next
    model in the chain is asked instead -- a question that has been explored and understood
    should not be lost to a tier having a bad minute. When every model is busy the last failure
    is raised, which the caller turns into something a reader can act on.
    """
    trouble: Exception | None = None
    for model in MODEL_CHAIN:
        try:
            return model, gemini.models.generate_content(model=model, contents=contents,
                                                          config=config)
        except AiUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - classified immediately below.
            if not _busy(exc):
                raise
            trouble = exc
    raise trouble if trouble is not None else RuntimeError("no model could be asked")


def _describe(name: str, result: dict) -> str:
    """A short line for the page's "what it looked up" list, taken from the tool's own note."""
    note = str(result.get("note") or result.get("error") or "").strip()
    if not note:
        return name.replace("_", " ")
    if len(note) <= 300:
        return note
    # Cut at a word rather than through one: these lines are read, not searched.
    cut = note[:300].rsplit(" ", 1)[0]
    return (cut or note[:300]).rstrip(",;: ") + "..."


def _pack(result: dict) -> str:
    """Serialize a tool result for the model, with a backstop on its size."""
    try:
        text = json.dumps(result, default=str)
    except (TypeError, ValueError):
        text = json.dumps({"error": "The tool returned something that could not be read."})
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '" (truncated)"}'
    return text


def answer(league_id: str, question: str, reader: dict | None = None) -> dict:
    """Answer one question about one league.

    ``reader`` names the connected manager, which is what lets a question phrased from his side
    -- "my team", "the second I traded" -- resolve to a real roster.

    Raises :class:`~services.ask_model.AiUnavailable` when there is no key to ask with; every
    other failure comes back as ``ok: False`` with something readable in ``error``.
    """
    from google.genai import types

    problem = _validate(question)
    if problem:
        return {"ok": False, "error": problem, "steps": [], "seconds": 0.0, "model": MODEL}

    gemini = client()
    config = _tool_config()
    contents = [types.Content(role="user", parts=[types.Part(text=question.strip())])]
    steps: list[dict] = []
    started = time.monotonic()
    rounds = 0

    while rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        try:
            model, response = _asked(gemini, contents, config)
        except AiUnavailable:
            raise
        except Exception as exc:
            return _failed(exc, steps, started)

        calls = list(getattr(response, "function_calls", None) or [])
        if not calls:
            return _finished(response, steps, started, rounds, model=model)
        if not response.candidates:
            return _failed(RuntimeError("the model returned no candidates"), steps, started)

        contents.append(response.candidates[0].content)
        parts = []
        for call in calls:
            call_started = time.monotonic()
            result = league_tools.run(league_id, call.name, dict(call.args or {}), reader=reader)
            steps.append({
                "tool": str(call.name or ""),
                "arguments": dict(call.args or {}),
                "seconds": round(time.monotonic() - call_started, 2),
                "summary": _describe(str(call.name or ""), result),
                "failed": bool(result.get("error")),
            })
            parts.append(types.Part.from_function_response(
                name=str(call.name or ""), response={"result": _pack(result)}))
        contents.append(types.Content(role="user", parts=parts))

    # Out of rounds: ask once for the answer, with the tools left out of the conversation so the
    # only thing left to do is write it.
    contents.append(types.Content(role="user", parts=[types.Part(
        text="Answer now, using only what the tool results above already returned, and lay it out "
             "as the shape rules ask: a sentence for one fact, the two-column table for several "
             "facts about one thing, a list or a table for several things. If they do not answer "
             "the question, say so.")]))
    try:
        model, final = _asked(gemini, contents, types.GenerateContentConfig(
            temperature=TEMPERATURE, system_instruction=SYSTEM_INSTRUCTION))
    except Exception as exc:
        return _failed(exc, steps, started)
    return _finished(final, steps, started, rounds, looked_up_too_long=True, model=model)


def _validate(question: str) -> str:
    text = str(question or "").strip()
    if not text:
        return "Ask a question about this league."
    if len(text) > 500:
        return "That question is too long. Keep it under 500 characters."
    return ""


def _text_of(response) -> str:
    try:
        return str(response.text or "").strip()
    except Exception:  # A response whose parts are all function calls has no text.
        return ""


def _finished(response, steps: list[dict], started: float, rounds: int,
              looked_up_too_long: bool = False, model: str = MODEL) -> dict:
    text = _text_of(response)
    if not text:
        text = ("I looked this league up but could not put the answer into words. "
                "Try asking again, or more specifically.")
    if looked_up_too_long:
        text += " That is as far as I could get within the lookups allowed for one question."
    return {
        "ok": True,
        "answer": text,
        "steps": steps,
        "seconds": round(time.monotonic() - started, 2),
        "model": model,
        "rounds": rounds,
        "usage": _usage(response),
    }


def _usage(response) -> dict:
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return {}
    return {
        "prompt_tokens": getattr(meta, "prompt_token_count", None),
        "output_tokens": getattr(meta, "candidates_token_count", None),
        "total_tokens": getattr(meta, "total_token_count", None),
    }


def _failed(exc: Exception, steps: list[dict], started: float) -> dict:
    """Turn an API failure into something a reader can act on."""
    message = str(exc) or exc.__class__.__name__
    lowered = message.casefold()
    if "quota" in lowered or "429" in lowered or "rate" in lowered:
        hint = "The Gemini free tier's limit was reached. Wait a minute and ask again."
    elif "api key" in lowered or "unauthenticated" in lowered or "permission" in lowered or "401" in lowered or "403" in lowered:
        hint = "The Gemini API key was rejected. Check the key in api-key.txt."
    elif "timeout" in lowered or "timed out" in lowered or "connection" in lowered:
        hint = "The Gemini API could not be reached. Check this machine's connection."
    else:
        hint = "The Gemini API returned an error."
    return {
        "ok": False,
        "error": f"{hint} ({exc.__class__.__name__}: {message[:300]})",
        "steps": steps,
        "seconds": round(time.monotonic() - started, 2),
        "model": MODEL,
    }
