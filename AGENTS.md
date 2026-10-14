# Working on DeepThreat

## A change is not finished until the changelog says so

`services/changelog.py` is the What's new notebook beside Ask AI, and it is the only place a
change is described for the person playing in the league. So a change is not finished when the
code works and the checks pass: it is finished when the notebook says, in the reader's words,
what somebody using the app would now notice. Write the release in the same working tree as the
change it describes — never as a follow-up — because a change committed without a line is a
change no reader is ever told about.

- Draft the lines from the commits the notebook has not been told about yet:
  `python tools/draft_changelog.py`. It reads `COVERED_THROUGH`, hands those commit messages to
  the model Ask AI uses, and prints a release to edit rather than writing a file to trust.
  `--write` puts it at the top of the file and moves the anchor; `--since <sha>` drafts a range
  named by hand. With no key it still gathers the commits and marks its output ROUGH.
- Edit what it prints. A line says what a reader would notice — "History opens in 5s instead of
  13" — and never how it was done: no file names, no function names, no thread counts, no
  "refactor". A number belongs in a line only if a commit message contains it, used exactly as
  written there.
- A release is one dict at the top of `RELEASES`, newest first: a date, a headline in the
  reader's words, and its lines as `(kind, text)` pairs. The kinds are `performance`, `visual`,
  `functionality` and `also`. A kind that is not one of them stops the app at startup rather
  than drawing a chip with nothing on it.
- Move `COVERED_THROUGH` onto the commit the release speaks for, so the next draft starts from
  the right place rather than drafting work already in the notebook. `--write` moves it for you;
  a release written by hand moves it by hand. It is a note to the drafting tool — nothing reads
  it while the app runs — and the tool keeps it honest by being written rather than remembered.

A change nobody outside the app would see — a refactor, a note like this one — still belongs in
the notebook as an `also` line, because somebody comparing two copies of the app may still want
to know that the ground moved.

## The checks this repository has

- `python tools/check_lineup_slots.py --league-id <id>` is the repository's own check: every
  starter lands in a slot, each team's columns sum to its score, Group View is the slot view
  collapsed, and the Season tab's slot averages and the Player Data season tables are the same
  weekly numbers added up. It reads Sleeper directly, needs no browser and no account, and takes
  one finished season committed under `data/history/`.
- `.github/workflows/lineup-slots.yml` runs that check on every push and pull request, and
  `render.yaml` holds a deploy until the checks pass. Point the repository variable
  `DEEPTHREAT_CHECK_LEAGUE_ID` at another league to check that league instead.
- `python tools/check_deployed_revisions.py` asks every deployed copy which revision it is running
  and compares it with the commit that was pushed, so a deploy that did not land is a line of
  output rather than a page somebody has to remember to open. It asks the hosts over the network
  and not this repository, so it is run after a deploy rather than in CI. A host that hands its
  copy nothing can be stamped at build time with `python tools/stamp_revision.py`; both are in
  DEPLOY.md.
