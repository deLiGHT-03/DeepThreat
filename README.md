# DeepThreat

A modular, local web app for Sleeper fantasy football leagues, with per-player shortcuts to current X posts and news coverage.

## Windows app

Run `release\DeepThreatSetup.exe` to install DeepThreat for the current Windows user. The installer creates Start Menu and optional desktop shortcuts; later installers upgrade the same installation while keeping saved connection settings.

The app opens in Firefox when available, otherwise in a resizable Edge app window. Your Sleeper username, manager, and last selected league are stored locally under `%APPDATA%\DeepThreat`. Use the small key button beside the season selector to clear them and return to the connection page.

## Run from source

1. Install Python 3.10 or newer.
2. From this folder, install dependencies: `python -m pip install -r requirements.txt`
3. For Ask AI, save a free Gemini API key in `api-key.txt` in this folder. The rest of the app does not need it.
4. Start the app: `python run.py`

The launcher starts a local-only server on an available port and opens it in Firefox (or Edge if Firefox is unavailable).

## Put it on the web

The same app runs on a Python host. `wsgi.py` is the application object a platform's WSGI server imports, and `waitress` is the server it is served with. `DEPLOY.md` has the whole of it — the two variables that matter (`SECRET_KEY` signs the session cookie; `GEMINI_API_KEY` is only for Ask AI), the start command, the health check, and what a free instance does differently — and `render.yaml` and the `Dockerfile` are that same setup expressed for a platform.

Nothing has to be provisioned alongside it: every page reads Sleeper over HTTPS at request time, so a hosted copy needs no database, no volume and no inbound access. The connection you last opened lives in the browser's session; the settings file is only a remembered default, and a server that cannot write one still serves every page.

## Check the page layout

The brand logo in the toolbar is positioned absolutely, floating above the heading band, so heading text has to keep out of its lane. `python tools/check_layout.py` drives a real browser over every page mode the app links to, at widths on both sides of the 981px breakpoint, and fails if the two ever overlap. It starts its own server and checks the league already saved in your settings; install the extra tooling first with `python -m pip install -r requirements-dev.txt`.

Exit code 0 means no overlap, 1 means text runs through the logo, and 2 means a page could not be measured — never silently a pass, because the logo only renders once Sleeper returns the account's leagues. Add `--base-url http://127.0.0.1:PORT` to check an app you already have open, or `--json out.json` to keep every measurement.

## Rebuild the Windows installer

Run `packaging\build_windows.ps1` in PowerShell on Windows. Install Inno Setup 6 to create the upgrade-safe installer in `release\DeepThreatSetup.exe`; the standalone application folder is written to `dist\DeepThreat`.

## Modules

- `run.py` starts the local server and opens Firefox; `wsgi.py` is what a hosting platform serves instead.
- `services/local_settings.py` stores the per-user connection settings outside the install folder.
- `app.py` defines the page routes.
- `services/sleeper.py` handles Sleeper data.
- `services/matchups.py` builds weekly position-group score comparisons.
- `services/player_card.py` builds one player's schedule, weekly points and season total.
- `services/player_data.py` builds the weekly and season Player Data views.
- `services/league_tools.py` answers individual questions about one league's players, games, trades, draft picks, rosters and standings; it is what Ask AI is allowed to look up.
- `services/ask_ai.py` runs the Ask AI question: it gives the model those lookups, runs what it asks for, and returns the answer with the lookups listed.
- `services/ask_model.py` finds the Gemini key and names the model.
- `services/seasonal.py` builds the Season table and its modes.
- `static/player-card.js` opens the player card from any player name, on a click.
- `templates/` contains the pages; `static/style.css` contains the visual styling.
- `tools/check_layout.py` measures the heading text against the brand logo in a real browser.

The player hub opens X's live search and Google News search for one player at a time. It does not fetch results for the full roster in the background.

## Trades

The Trades page lists one manager's own trades beside the whole league's, for one season or one week of it. The column is the connected manager's by default, and a dropdown in its header points it at any manager the league has had — the manager is followed by his Sleeper user id rather than by a roster, so a rename does not split him in two, a manager who has since left the league is still one the column can show, and every sentence and label in it is written from whoever is being shown: "Your Trades — You sent", or "bkodae's Trades — bkodae sent". Opening a week of somebody's trades is that week of *those* trades, and a search made while a manager is being viewed keeps him. Above both columns sits one search box, and it reads every season the league has played, because a career is not a season: type a player's name, a team's name, a manager's username, or a pick ("2025 1st", "2.02", or a year on its own — "2024" — for every 2024 pick that changed hands). Names are matched loosely — part of a surname, a missing letter or a swapped pair all count — but only the players and teams this league has actually traded can be found. A typed name is read as a player's first and as a team's only when no player answers to it, since teams are often named after players. A team is followed by its manager, whose user id survives a rename, so searching a team name finds the seasons it traded under that name and the ones it traded under an earlier one.

A traded pick is a button, because what a manager wants from one is what it became. Clicking it opens a panel with the player taken with it, the place in the draft that happened ("1.08", overall 8) and the team that made the pick, then the route the pick took to get there, each hand-off with the week it was filed. However many teams it passed through, the panel ends with a player: the draft's own order places the pick, and a seat that order leaves out — a slot nobody holds, or one held by a manager who has since left the league — is placed by the team the trades say ended up with it, which is the team that made that seat's pick. Where the last recorded move and the draft's own seat disagree, the player is still named and the missing move is said beneath him. A pick whose season has not been drafted yet says so instead, because there is no player to name. A page can be open on an earlier season, and a pick looked up from one is read the same way: the trace — and every search — is anchored on the newest season of the league's chain rather than on the season being shown, so a 2023 pick seen from the 2019 page still names the player taken with it instead of reporting a draft that has since been held as one that has not happened.

## Ask AI

Ask AI answers questions about one league from that league's own Sleeper records — a player's weekly points and the last time he scored under a total, the exact place a player was drafted ("3.04, the 28th pick overall", and whether it was the startup or the rookie draft) together with whose pick it was and the trade that brought it there — the team it came from and what each side received — who a round of a draft held, the trades a team made and what each side received, what became of a draft pick that was traded, a season's records and its champion, and a whole set of players ranked by their best single season ("which third round picks have scored the most in a season"). It is an ordinary tab beside History, and every page links to it.

A question that compares many players is answered by adding up the entire set and ordering it, not by naming the best of the handful that happened to be looked up. Every player the league drafted in that round is assembled, each one's weeks are summed into season totals, and the best of those totals is what gets ranked — so the ranking behind "which third round picks scored the most" covers all of that round's players, rather than a few flattering game logs. A week the season has not finished is not read, a week that scored nothing is not a game, and a filter narrows the set to a draft round, a draft year or a position; a season alone ranks that season's scores.

An answer takes the shape of the question: one fact is a short paragraph, a set of things that share fields — a round of a draft, a week's scores, rosters' records, players ranked by their best season — comes back as a table, and a set of things that do not, such as a list of trades, comes back as one line each. A figure is stated with the season and week it belongs to, and a draft pick is never a round guessed from a nearby pick.

A position in an answer wears the same badge every roster row wears, and a draft pick a gold chip of its own, so both can be picked out at a glance — in a table and in a sentence alike. The app reads those out of the answer's own text: QB, RB, WR, TE, DEF and DST as whole words, and a pick as the year and the round ("2025 1st") or a place in one ("3.04", "3rd Rd (3.10)"). A place is only read as a pick where a pick word sits beside it or the column is one that names picks, so a table of weekly scores keeps its figures as figures, and a figure is never dressed as a pick.

A draft pick is never reported by where it landed alone: the pick behind a drafted player is named with the team it originally belonged to and the trade that moved it, with what each side gave and received, whether or not that was asked for. Where a pick cannot be traced — Sleeper's draft order leaves out the slot, as it does when the manager who held it has left the league — the answer says so rather than leaving the reader to assume the pick was never traded.

The answers are written by Gemini (`gemini-3.5-flash-lite`). The model is never handed the league and is never shown another one: it is given a fixed set of read-only lookups — player game logs, trades, draft picks, rosters, standings, weekly scores, draft boards, season rankings and the league's record book — and the answer is assembled from what those return, so a figure in an answer is a figure the app read out of the league itself. The lookups behind an answer are listed under it. A traded pick is placed in its draft by the draft's own order, so "what did this pick become" is answered with one pick and the rookie taken there — and a pick that changed hands, however many times, still ends with the player it became: a seat the draft's order leaves out is placed by the team the trades say ended up with the pick, which is the team that made that seat's pick. Where the last recorded move and the draft's own seat disagree, the seat still names the player and the disagreement is reported beside him, because a move missing from Sleeper's feed is not a reason to leave the question unanswered. Only where the records genuinely cannot settle a question — a season holding two drafts, or two teams' picks of one round that cannot be told apart — does the answer say so rather than choosing.

It needs a free Gemini API key, and it is off until one is present — the tab appears only when a key is found. Save the key in `api-key.txt` beside the app, or in `%APPDATA%\DeepThreat\api-key.txt` for an installed copy, or set `GEMINI_API_KEY` in the environment. The key file is read from the app folder or the per-user settings folder, is never bundled into the Windows build, and is never shown back in the app.

The Matchups page loads one selected week at a time, features the connected manager's matchup, and compares points by lineup slot. QB and Superflex are combined as QB; all regular flex slots are combined as FLEX regardless of who filled them. It opens on the slot view, which reassigns only players who started in that matchup into best-scoring eligible slots, with slot labels and order derived from the league's starting lineup settings. Its Broad View checkbox switches to the submitted lineup grouped by wider positions instead, without leaving the page. Its week tabs run through the regular season and then the league's playoff rounds, named P1, P2 and so on with the final called SB — one round a week, as many rounds as the league's own playoff bracket holds, so the rounds can be browsed before any of them has been played. A playoff round is a week like any other here: the teams that have an opponent are shown, and a team left without one is not on the page. The Season tab offers the same two views and opens on the same one, and compares each team's average slot production with the league average for that same slot, using completed weeks with available scores.

The Impact Players tab is hidden for now. Its pages and routes stay live, so opening a page with `?impact=1` still works, and setting `show_impact_players` to `True` in `app.py` puts the tab back on every page that links to it.

Clicking a player's name — on the Matchups page, or on any Player Data table — opens that player's card: his headshot, the rate he scores at with the games behind it, his NFL schedule for the season with the fantasy points he scored in this league each week, and his season total. Clicking the name again closes it, as does clicking anywhere else, pressing Escape, or scrolling the page; nothing opens merely because the pointer passed over a name. The list covers the league's whole regular season, marks bye weeks, dims weeks not yet played, and counts a week as a game only when Sleeper recorded him on the field — so a bye or a week he sat out shows a dash and stays out of the total. Points come from the same weekly matchup records the rest of the app reads, so a card agrees with the season views, with one deliberate difference: a card totals everything a player scored this season even if he changed teams, where the Team tab counts only the weeks he spent with the team that holds him now.

The Player Data page shows one week at a time, or the season so far. Its Season tab lists every rostered player's points per game by position in the same tables the week views use, and its Team tab groups those same numbers by roster. Both season views can drop each player's lowest or highest week from the totals. They run past the end of the regular season into the league's playoff weeks, which count once the league has scored them, so a finished season reads whole — up to a 17-week sample for a league whose playoffs end in week 17. The week tabs here are the same ones the Matchups page shows, playoff rounds included, and a week opened on a round says which round it is.
