# DeepThreat

A modular, local web app for Sleeper fantasy football leagues, with per-player shortcuts to current X posts and news coverage.

## Windows app

Run `release\DeepThreatSetup.exe` to install DeepThreat for the current Windows user. The installer creates Start Menu and optional desktop shortcuts; later installers upgrade the same installation while keeping saved connection settings.

The app opens in Firefox when available, otherwise in a resizable Edge app window. Your Sleeper username, manager, and last selected league are stored locally under `%APPDATA%\DeepThreat`. Use the small key button beside the season selector to clear them and return to the connection page.

## Run from source

1. Install Python 3.10 or newer.
2. From this folder, install dependencies: `python -m pip install -r requirements.txt`
3. Start the app: `python run.py`

The launcher starts a local-only server on an available port and opens it in Firefox (or Edge if Firefox is unavailable).

## Check the page layout

The brand logo in the toolbar is positioned absolutely, floating above the heading band, so heading text has to keep out of its lane. `python tools/check_layout.py` drives a real browser over every page mode the app links to, at widths on both sides of the 981px breakpoint, and fails if the two ever overlap. It starts its own server and checks the league already saved in your settings; install the extra tooling first with `python -m pip install -r requirements-dev.txt`.

Exit code 0 means no overlap, 1 means text runs through the logo, and 2 means a page could not be measured — never silently a pass, because the logo only renders once Sleeper returns the account's leagues. Add `--base-url http://127.0.0.1:PORT` to check an app you already have open, or `--json out.json` to keep every measurement.

## Rebuild the Windows installer

Run `packaging\build_windows.ps1` in PowerShell on Windows. Install Inno Setup 6 to create the upgrade-safe installer in `release\DeepThreatSetup.exe`; the standalone application folder is written to `dist\DeepThreat`.

## Modules

- `run.py` starts the server and opens Firefox.
- `services/local_settings.py` stores the per-user connection settings outside the install folder.
- `app.py` defines the page routes.
- `services/sleeper.py` handles Sleeper data.
- `services/matchups.py` builds weekly position-group score comparisons.
- `services/player_card.py` builds one player's schedule, weekly points and season total.
- `services/player_data.py` builds the weekly and season Player Data views.
- `services/seasonal.py` builds the Season table and its modes.
- `static/player-card.js` opens the player card from any player name, on a click.
- `templates/` contains the pages; `static/style.css` contains the visual styling.
- `tools/check_layout.py` measures the heading text against the brand logo in a real browser.

The player hub opens X's live search and Google News search for one player at a time. It does not fetch results for the full roster in the background.

The app does not use Gemini. `api-key.txt` is not read and is excluded from the Windows build.

The Matchups page loads one selected week at a time, features the connected manager's matchup, and compares points by lineup slot. QB and Superflex are combined as QB; all regular flex slots are combined as FLEX regardless of who filled them. It opens on the slot view, which reassigns only players who started in that matchup into best-scoring eligible slots, with slot labels and order derived from the league's starting lineup settings. Its Broad View checkbox switches to the submitted lineup grouped by wider positions instead, without leaving the page. Its week tabs run through the regular season and then the league's playoff rounds, named P1, P2 and so on with the final called SB — one round a week, as many rounds as the league's own playoff bracket holds, so the rounds can be browsed before any of them has been played. A playoff round is a week like any other here: the teams that have an opponent are shown, and a team left without one is not on the page. The Season tab offers the same two views and opens on the same one, and compares each team's average slot production with the league average for that same slot, using completed weeks with available scores.

The Impact Players tab is hidden for now. Its pages and routes stay live, so opening a page with `?impact=1` still works, and setting `show_impact_players` to `True` in `app.py` puts the tab back on every page that links to it.

Clicking a player's name — on the Matchups page, or on any Player Data table — opens that player's card: his headshot, the rate he scores at with the games behind it, his NFL schedule for the season with the fantasy points he scored in this league each week, and his season total. Clicking the name again closes it, as does clicking anywhere else, pressing Escape, or scrolling the page; nothing opens merely because the pointer passed over a name. The list covers the league's whole regular season, marks bye weeks, dims weeks not yet played, and counts a week as a game only when Sleeper recorded him on the field — so a bye or a week he sat out shows a dash and stays out of the total. Points come from the same weekly matchup records the rest of the app reads, so a card agrees with the season views, with one deliberate difference: a card totals everything a player scored this season even if he changed teams, where the Team tab counts only the weeks he spent with the team that holds him now.

The Player Data page shows one week at a time, or the season so far. Its Season tab lists every rostered player's points per game by position in the same tables the week views use, and its Team tab groups those same numbers by roster. Both season views can drop each player's lowest or highest week from the totals. They run past the end of the regular season into the league's playoff weeks, which count once the league has scored them, so a finished season reads whole — up to a 17-week sample for a league whose playoffs end in week 17. The week tabs here are the same ones the Matchups page shows, playoff rounds included, and a week opened on a round says which round it is.
