"""Flask application factory and page routes."""

from urllib.parse import quote_plus
from pathlib import Path
import hashlib
import mimetypes
import os
import secrets
import sys

from flask import (Flask, Response, flash, jsonify, make_response, redirect,
                   render_template, request, session, url_for)

from services.sleeper import (CDN_SECONDS, MAX_NFL_WEEK, SleeperError, display_handle,
                              get_cdn_file, get_league_dashboard, get_json,
                              get_league_season_history, get_user, get_user_account_leagues)
from services.matchups import (get_default_week,
                               get_week_matchups, is_week_started)
from services.seasonal import get_seasonal_impact_summary, get_seasonal_summary
from services.player_data import get_player_data
from services.player_card import get_player_card
from services.player_pace import get_card_pace
from services.pace_board import get_pace_board
from services.trades import get_trades_summary
from services import build_info
from services import draft_picks
from services import pick_query
from services import rate_limit
from services.history import get_history_summary
from services.preload import preload_status, start_preload
from services.local_settings import clear_connection, load_connection, save_connection
from services import ask_ai
from services import league_tools
from services.ask_model import MODEL as AI_MODEL, AiUnavailable, available as ai_available
from services.ask_model import key_paths as ai_key_paths
from services.changelog import releases as changelog_releases


def session_secret() -> str:
    """The key the session cookie is signed with.

    Served to the internet this is a real secret -- anyone who knows it can mint a cookie that
    says they are somebody -- so a hosted copy takes it from the environment (``SECRET_KEY`` or
    ``FLASK_SECRET_KEY``, whichever the platform sets) and never from a constant in this file.
    Run from source there is nothing to leak and nothing to configure, so one is generated per
    start: the only cost is that a cookie from an earlier run is ignored, and the connection is
    remembered in the settings file as well.
    """
    for variable in ("SECRET_KEY", "FLASK_SECRET_KEY"):
        value = (os.environ.get(variable) or "").strip()
        if value:
            return value
    return secrets.token_hex(32)


# Python's own table of file types has no entry for the logo's format, and on a desktop the
# operating system answers instead -- which is exactly how the pages' one image can look right
# on this machine and arrive as ``application/octet-stream`` from a Linux host, where there is no
# registry and a slim container ships no ``/etc/mime.types`` either. A browser renders the image
# regardless, because an ``<img>`` does not read the type, but everything else that looks at the
# response does: a proxy deciding what to cache, a download, a scanner. So the type is declared
# here rather than left to whatever the platform happens to know.
mimetypes.add_type("image/webp", ".webp")

#: What each static file's URL is stamped with, remembered between requests: the file's size
#: and modification time, and the digest taken from its bytes at that moment.
_asset_stamps: dict[str, tuple[int, int, str]] = {}


def asset_stamp(filename: str, static_folder: str) -> str:
    """The digest of one static file, or "" when it cannot be read.

    Reading the file is what this costs, so the answer is kept and only read again once the
    file's size or modification time has moved. Against the alternative -- an hour or a year of
    caching on a URL that never changes, and every visitor re-asking for every stylesheet --
    one ``stat`` per asset per page is the cheaper half of the bargain.
    """
    path = Path(static_folder) / filename
    try:
        info = path.stat()
    except OSError:
        return ""
    remembered = _asset_stamps.get(filename)
    if remembered is not None and remembered[:2] == (info.st_mtime_ns, info.st_size):
        return remembered[2]
    try:
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""
    _asset_stamps[filename] = (info.st_mtime_ns, info.st_size, digest)
    return digest


def create_app() -> Flask:
    app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    app = Flask(__name__, template_folder=str(app_root / "templates"),
                static_folder=str(app_root / "static"))
    app.config["SECRET_KEY"] = session_secret()
    app.jinja_env.globals["quote_plus"] = quote_plus

    def static_asset(filename: str) -> str:
        """A static file's URL, stamped with a digest of the file's own bytes.

        These files are served with a year of caching, which is only honest while the URL
        changes whenever the file does. The stamp is the file's own content, so editing a
        stylesheet produces a URL nothing has ever cached; without it, a year would mean a fix
        reaching a returning visitor a year late.
        """
        stamp = asset_stamp(filename, app.static_folder or "")
        return url_for("static", filename=filename, v=stamp or None)

    # Every template builds its asset URLs here rather than with url_for("static", ...), so
    # nothing can take the long-lived caching below without also carrying a stamp.
    app.jinja_env.globals["asset_url"] = static_asset
    # What a position is called when a table is grouped under one, rather than the code a lineup
    # and a chip name it by. It lives here rather than in each page that groups by position, so
    # the Player Data tables and the Pace board call a running back the same thing; a position
    # this list has never heard of is named by its own code, which is what a league with a
    # position nobody expected should be shown.
    app.jinja_env.globals["position_names"] = {
        "QB": "Quarterback", "RB": "Running Back", "WR": "Wide Receiver", "TE": "Tight End",
        "K": "Kicker", "DEF": "Defense", "DL": "Defensive Line", "LB": "Linebacker",
        "DB": "Defensive Back", "IDP": "IDP", "P": "Punter", "OL": "Offensive Line",
    }
    # The Impact Players page is hidden until its shape is settled. Its routes and data
    # stay live, so this one flag brings the tab back on every page that links to it.
    app.jinja_env.globals["show_impact_players"] = False
    # Trades is built but not shipped. The routes and data stay live, so opening a trades
    # page works either way; this flag is what puts the button on every page that links to
    # it. Set it to False to take the button back off before the page goes live.
    app.jinja_env.globals["show_trades"] = True
    # History reads every season of the league, which is a lot of Sleeper data for one page.
    # The route stays live either way, so this flag only decides whether the button is there.
    app.jinja_env.globals["show_history"] = True
    # Ask AI is off until a Gemini key is on the machine. The route stays live either way, so
    # this flag only decides whether the tab is offered; the page itself says where a key goes.
    app.jinja_env.globals["show_ask_ai"] = ai_available()
    # What's new, written for the reader rather than for the code: services/changelog.py is the
    # one place a change is written down for somebody using the app, and the notebook on every
    # page draws from here. Built once at startup, so a release filed under a kind with no name
    # stops the app instead of drawing itself a chip with nothing written on it.
    app.jinja_env.globals["changelog"] = changelog_releases()

    def league_switcher_data(username: str, manager_id: str, season: str,
                             current_league_id: str = ""):
        connected_username = username.strip()
        if not connected_username and manager_id:
            try:
                connected_username = get_user(manager_id).get("username", "")
            except SleeperError:
                return []
        if not connected_username or not season:
            return []
        try:
            _, leagues = get_user_account_leagues(connected_username, str(season))
        except SleeperError:
            return []
        current = str(current_league_id or "")
        for league in leagues:
            league["is_current"] = str(league.get("league_id") or "") == current
        return leagues

    def league_season_options(league_id: str):
        try:
            return get_league_season_history(str(league_id))
        except SleeperError:
            return []

    def current_season_history(league_id: str, username: str, manager_id: str):
        """Find the active-season league record whose history contains this league."""
        connected_username = username.strip()
        if not connected_username and manager_id:
            try:
                connected_username = get_user(manager_id).get("username", "")
            except SleeperError:
                return None
        if not connected_username:
            return None
        try:
            state = get_json("/state/nfl") or {}
            seasons = dict.fromkeys(
                str(value) for value in (state.get("league_season"), state.get("season")) if value
            )
            for current_season in seasons:
                _, current_leagues = get_user_account_leagues(connected_username, current_season)
                for current_league in current_leagues:
                    history = league_season_options(str(current_league.get("league_id", "")))
                    if any(item["league_id"] == str(league_id) for item in history):
                        return history
        except SleeperError:
            return None
        return None

    def season_history_for(league_id: str, username: str, manager_id: str):
        active_history = current_season_history(league_id, username, manager_id)
        if active_history:
            return active_history[0]["league_id"], active_history
        history_id = (request.args.get("history_id", "").strip()
                      or session.get("season_history_league_id", "") or league_id)
        history = league_season_options(history_id)
        if history and not any(item["league_id"] == str(league_id) for item in history):
            history_id, history = league_id, league_season_options(league_id)
        return history_id, history

    def newest_season_league(league_id: str, username: str, manager_id: str) -> str:
        """Return the newest season's league in the history that holds this league.

        The connection remembers the league you last opened, and that is often an older season
        you were reading through the switcher. The page lists that league's seasons newest
        first, so opening on the first of them is what stops the app starting on a previous
        season. The league is only replaced by one of its own seasons: a history that does not
        hold it is left alone rather than trusted.
        """
        _, history = season_history_for(league_id, username, manager_id)
        if history and any(item["league_id"] == str(league_id) for item in history):
            return str(history[0]["league_id"])
        return str(league_id)

    #: Which limit a request is counted in, by the endpoint that answers it. Anything not named
    #: here is a page render, which is the costly thing to guess your way through. Static files
    #: and the health check are counted in none: one is a file off the disk, and the other has to
    #: answer while a visitor is being throttled, or the platform would replace an instance that
    #: is doing exactly what it should.
    limited = {
        "ask_question": "ask",
        "preload": "preload",
        "media": "media",
        "week_status": "data",
        "player_card": "data",
        "player_pace": "data",
        "compare_player": "data",
        "pick_detail": "data",
        "league_players": "data",
    }

    @app.before_request
    def limit_requests():
        """Count this request before any work is done for it, and refuse it if there is no room.

        A served copy answers strangers, so this is where being one of them stops: a crawler
        walking the app, a script looping on a page and a visitor asking a hundred questions are
        the same thing to this hook, and all of them are answered with 429 and how long to wait.
        """
        endpoint = request.endpoint or ""
        if endpoint in ("static", "healthz", "robots"):
            return None
        bucket = limited.get(endpoint, "page")
        # The Preload button starts a load with a POST and watches it with a GET. Only the start
        # is costly -- watching is what the button does while it works -- so the POST is held to
        # the tighter limit and the watching falls in with every other JSON read.
        if endpoint == "preload" and request.method != "POST":
            bucket = "data"
        who = rate_limit.visitor(request.headers, request.remote_addr or "")
        allowed, retry_after = rate_limit.check(bucket, who)
        if allowed:
            return None
        if rate_limit.first_block(bucket, who):
            app.logger.info("Refused a %s request from %s for %ss", bucket, who, retry_after)
        wait = rate_limit.describe_wait(retry_after)
        if bucket == "page":
            response = make_response(render_template("too_many.html", wait=wait))
        elif bucket == "ask":
            # The Ask page shows this the way it shows a question it could not answer, so a
            # refusal reads as an answer rather than as a page that broke.
            response = jsonify(
                ok=False, steps=[], seconds=0.0,
                error="This copy of DeepThreat answers only so many questions at a time, and "
                      f"that many have just been asked. Try again in {wait}.",
            )
        else:
            response = jsonify(
                ok=False,
                error="This copy of DeepThreat answers one thing at a time and is busy. Try "
                      f"again in {wait}.",
            )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.after_request
    def cache_stamped_assets(response):
        """Let a browser keep a static file until its URL changes, instead of re-asking for it.

        One year is safe here for the one reason that makes it safe anywhere: the URL carries a
        digest of the file it names (``static_asset``), so an edited file is a new URL and the
        cached copy of the old one is never asked for again. ``immutable`` is the half that
        matters day to day -- it is what stops so much as a reload from revalidating, which is
        what turned every page view into nine conditional round trips for a handful of files
        that had not changed since the last one.

        The stamp has to be in the URL for any of that to be honest, so a static file asked for
        without one keeps the default revalidation: the rule is that a long cache and a stamp
        arrive together or not at all, and nothing needs to be trusted to keep that true.
        """
        if request.endpoint == "static" and request.args.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.after_request
    def keep_out_of_search(response):
        """Tell every crawler not to index this, on the responses a page's own tag cannot reach.

        The header and the meta tag say the same thing and neither is load-bearing alone: a
        crawler that never parses the HTML never sees the tag, and one that does not read headers
        still stops at the tag. The JSON, the relayed images and the redirects only have this.
        """
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    @app.get("/robots.txt")
    def robots():
        """Ask crawlers to stay out.

        Written down rather than left to the header, because this is the first thing a well
        behaved crawler asks for and answering it plainly costs both sides less than being
        ignored does. It is a request, not a defence -- the limits are the defence.
        """
        response = Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.get("/")
    def index():
        saved = load_connection()
        username = saved.get("username") or session.get("username", "")
        manager_id = saved.get("manager_id") or session.get("manager_id", "")
        league_id = saved.get("league_id") or session.get("league_id")
        if league_id:
            # Start on the newest season in this league's history, not the season that was
            # browsed last: the season switcher lists that one first, so this is the season
            # the page opens showing.
            league_id = newest_season_league(league_id, username, manager_id)
            session.update(league_id=league_id, username=username, manager_id=manager_id,
                           season_history_league_id=saved.get("history_league_id", league_id))
            return redirect(url_for("matchups", league_id=league_id,
                                    username=username, manager_id=manager_id))
        return render_template("index.html", username=username, hide_brand_logo=True)

    @app.get("/connect")
    def connect():
        session.clear()
        return render_template("index.html", username=load_connection().get("username", ""),
                               hide_brand_logo=True)

    @app.get("/reset-username")
    def reset_username():
        clear_connection()
        session.clear()
        return render_template("index.html", username="", hide_brand_logo=True)

    @app.post("/leagues")
    def leagues():
        username = request.form.get("username", "").strip()
        season = request.form.get("season", "").strip()
        if not username or not season.isdigit():
            flash("Enter a Sleeper username and a valid season.", "error")
            return redirect(url_for("index"))
        try:
            manager, leagues = get_user_account_leagues(username, season)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc), username=username,
                                   hide_brand_logo=True), 400
        username = manager.get("username") or username
        save_connection(username, manager.get("user_id", ""))
        # The page says the account's name back to its owner, capitalised the way he writes it,
        # while the handle itself is what is saved and carried -- Sleeper's API answers to the
        # handle, and a display name is not always a name it would recognise.
        return render_template("leagues.html", username=username,
                               account_name=display_handle(manager.get("display_name"),
                                                           username) or username,
                               manager_id=manager["user_id"], season=season, leagues=leagues)

    @app.route("/league/<league_id>", methods=["GET", "POST"])
    def league(league_id: str):
        username = request.values.get("username", "").strip()
        manager_id = request.values.get("manager_id", "").strip()
        if username and not manager_id:
            try:
                manager_id = get_user(username)["user_id"]
            except SleeperError:
                manager_id = ""
        try:
            dashboard = get_league_dashboard(league_id)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc)), 400
        username = username or session.get("username", "")
        manager_id = manager_id or session.get("manager_id", "")
        session.update(league_id=league_id, username=username, manager_id=manager_id,
                       season=str(dashboard["league"].get("season", "")),
                       season_history_league_id=request.values.get("history_id", "").strip() or league_id)
        save_connection(username, manager_id, league_id,
                        request.values.get("history_id", "").strip() or league_id)
        return redirect(url_for("matchups", league_id=league_id,
                                username=username, manager_id=manager_id))

    @app.get("/league/<league_id>/matchups")
    def matchups(league_id: str):
        raw_week = request.args.get("week")
        if raw_week is None:
            raw_week = str(get_default_week(league_id))
        if not raw_week.isdigit():
            return render_template("index.html", error="Choose a valid week."), 400
        week = int(raw_week)
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        # The week view opens on slot-level rows; the Group View switch over the matchups merges
        # those same normalized slots into wider position groups. Impact is measured per slot, so
        # it keeps that grouping either way. The address parameter keeps its old name -- it was
        # `broad` when the control was called Broad View -- because these addresses are pasted
        # into league chats and an old one still has to open the view it was shared for.
        broad = request.args.get("broad") == "1"
        impact = request.args.get("impact") == "1"
        normalized = not broad or impact
        try:
            weekly = get_week_matchups(league_id, week, username, manager_id, normalized, impact)
        except (SleeperError, ValueError) as exc:
            return render_template("index.html", error=str(exc)), 400
        save_connection(username, manager_id, league_id, history_id)
        return render_template("matchups.html", weekly=weekly,
                               username=username, manager_id=manager_id,
                               season=str(weekly["league"].get("season", "")),
                               current_league_id=weekly["league"]["league_id"],
                               switch_page="matchups", switch_week=week,
                               switch_broad=broad,
                               switch_impact=impact,
                               season_options=history, history_league_id=history_id,
                               current_league_name=str(weekly["league"].get("name", "")),
                               league_options=league_switcher_data(
                                   username, manager_id,
                                   history[0]["season"] if history else str(weekly["league"].get("season", "")),
                                   weekly["league"]["league_id"]))

    @app.route("/league/<league_id>/preload", methods=["GET", "POST"])
    def preload(league_id: str):
        """Load every page's data for a league, or report how that load is going."""
        if request.method == "POST":
            start_preload(league_id,
                          username=request.args.get("username", ""),
                          manager_id=request.args.get("manager_id", ""),
                          force=request.args.get("refresh") == "1")
        response = jsonify(preload_status(league_id))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz():
        """A liveness endpoint for a hosting platform.

        It answers without reading Sleeper, so a health check never waits on a page's worth of
        requests and never fails because the upstream is slow. Deliberately not a status of the
        league: it says this process is up, which is the only thing the platform can act on.

        It also names the revision it is running, which is the one thing a page cannot say: a
        host that has not released the newest commit looks exactly like one that has, so the
        commit is what tells the copy being read apart from the copy that was pushed. Read from
        the host's own variables, or from the checkout when there are none; see
        :mod:`services.build_info`.
        """
        response = jsonify(ok=True, service="deepthreat", **build_info.describe())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/media/<path:path>")
    def media(path: str):
        """Relay a Sleeper CDN image from this origin, so a snapshot can embed it."""
        try:
            payload, content_type = get_cdn_file(path)
        except SleeperError:
            return "", 404
        response = Response(payload, mimetype=content_type)
        response.headers["Cache-Control"] = f"public, max-age={CDN_SECONDS}"
        return response

    @app.get("/api/nfl/<season>/week/<int:week>/started")
    def week_status(season: str, week: int):
        if not season.isdigit() or not 1 <= week <= MAX_NFL_WEEK:
            return jsonify(started=None)
        try:
            return jsonify(started=is_week_started(season, week))
        except SleeperError:
            return jsonify(started=None)

    @app.get("/league/<league_id>/player/<player_id>/card")
    def player_card(league_id: str, player_id: str):
        """Serve one player's season card to the hover panel that asks for it."""
        try:
            card = get_player_card(league_id, player_id)
        except SleeperError as exc:
            return jsonify(error=str(exc)), 404
        response = jsonify(card)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/league/<league_id>/player/<player_id>/pace")
    def player_pace(league_id: str, player_id: str):
        """Serve one player's 17-game pace, asked for only when the card's button is pressed.

        The card's own reading of the projection -- the line with the work behind a figure on it,
        which is the same figures the Pace board draws a faded column of, read as a row. What a
        ranking or an Ask AI answer about a player wants is the scored line alone, and that is
        what :func:`services.player_pace.get_player_pace` still returns.
        """
        try:
            pace = get_card_pace(league_id, player_id)
        except SleeperError as exc:
            return jsonify(error=str(exc)), 404
        response = jsonify(pace)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/league/<league_id>/player/<player_id>/compare")
    def compare_player(league_id: str, player_id: str):
        """Set a second player beside the one on a card, for the box under his pace.

        The card's own player is named by id rather than by the name printed on it, because a
        league can hold two players who read the same and the card is not in doubt about which
        one it belongs to. Everything else is the comparison Ask AI and the compare page draw,
        read from the one lookup the three of them share, so a card cannot disagree with either.
        """
        other = request.args.get("other", "").strip()
        if not other:
            return jsonify(error="Name a player to compare with."), 400
        # A card is always about one season, so the comparison projects the season the card is
        # showing rather than whichever season the league has reached since.
        try:
            season = str(get_league_dashboard(league_id)["league"].get("season") or "")
            result = league_tools.compare_paces(league_id, "", other,
                                                season=season, player_id=player_id)
        except SleeperError as exc:
            return jsonify(error=str(exc)), 503
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/league/<league_id>/seasonal")
    def seasonal(league_id: str):
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        defense = request.args.get("defense") == "1"
        true_record = request.args.get("true_record") == "1"
        ppg = False
        impact = request.args.get("impact") == "1"
        # Same two views as the week page, and the same default: slot-level rows unless the
        # Group View switch asks for the normalized slots merged into position groups. Defense
        # and impact are slot views.
        broad = request.args.get("broad") == "1" and not defense and not impact
        normalized = not broad or defense or impact
        try:
            if impact:
                summary = get_seasonal_impact_summary(league_id, username, manager_id=manager_id)
            else:
                summary = get_seasonal_summary(league_id, username, manager_id=manager_id,
                                               normalized=normalized, defense=defense,
                                               true_record=true_record, ppg=ppg)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc)), 400
        save_connection(username, manager_id, league_id, history_id)
        return render_template("seasonal.html", summary=summary,
                               username=username, manager_id=manager_id,
                               season=str(summary["league"].get("season", "")),
                               current_league_id=summary["league"]["league_id"],
                               switch_page="seasonal", switch_broad=broad,
                               switch_defense=defense,
                               switch_true_record=true_record,
                               switch_impact=impact,
                               season_options=history, history_league_id=history_id,
                               current_league_name=str(summary["league"].get("name", "")),
                               league_options=league_switcher_data(
                                   username, manager_id,
                                   history[0]["season"] if history else str(summary["league"].get("season", "")),
                                   summary["league"]["league_id"]))

    @app.get("/league/<league_id>/trades")
    def trades(league_id: str):
        """This season's trades: the connected manager's own, with the league at their side.

        A week tab narrows both columns to the trades filed against that week, so the page
        answers "what happened in week 5" as readily as "what happened all season", and a name
        searches every season the league has played for the trades it was in -- a player's, or
        a team's.
        """
        raw_week = request.args.get("week", "").strip()
        week = int(raw_week) if raw_week.isdigit() else 0
        # One box searches for everything a manager looks for: a pick ("2025 1st", "2.02", or a
        # year on its own like "2024") is read as a pick, and anything that is not a pick is read
        # as a name -- a player's first, then a team's or its owner's. The reading is what
        # decides, so a name is never mistaken for a pick.
        typed = request.args.get("player", "").strip()
        pick = typed if pick_query.read(typed) else ""
        player = "" if pick else typed
        # Whose trades the left column shows. It is a manager's user id, so the column follows
        # one person across the seasons of a chain rather than one seat of one season; absent,
        # the page shows the connected manager, which is what it opens on.
        owner = request.args.get("owner", "").strip()
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        try:
            # The search reads every season the league has played, so it is anchored on the
            # newest season rather than on the one this page is showing: a search made from an
            # older season would otherwise stop at it and miss every trade since.
            summary = get_trades_summary(league_id, username, manager_id=manager_id, week=week,
                                         player=player, pick=pick, chain_league_id=history_id,
                                         owner=owner)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc)), 400
        save_connection(username, manager_id, league_id, history_id)
        return render_template("trades.html", summary=summary, username=username,
                               manager_id=manager_id,
                               season=str(summary["league"].get("season", "")),
                               current_league_id=summary["league"]["league_id"],
                               switch_page="trades",
                               season_options=history, history_league_id=history_id,
                               current_league_name=str(summary["league"].get("name", "")),
                               league_options=league_switcher_data(
                                   username, manager_id,
                                   history[0]["season"] if history else str(summary["league"].get("season", "")),
                                   summary["league"]["league_id"]))

    @app.get("/league/<league_id>/history")
    def history(league_id: str):
        """Every season this league has played: its record book and its leaderboards.

        With no season asked for, the page answers for the whole chain. A season asks for that
        year on its own -- the same book, its leaders drawn from that season -- and is read from
        the chain's newest season either way, because that is what describes the whole history.

        The league in the address is the season the reader is in when the chain holds it -- the one
        the switcher above the title moved him to -- and the chain's newest season otherwise, which
        is the season every other tab opens on. A year is then a look at the book rather than a move
        to that year's league: the tabs above it, the league switcher beside them, the All-Time chip
        and the address itself all stay on the season the reader is in, and the scope changes under
        them and nothing else.
        """
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        season = request.args.get("season", "").strip()
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        try:
            summary = get_history_summary(history[0]["league_id"] if history else league_id,
                                          username, manager_id=manager_id, season=season)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc)), 400
        # It is the chain that says which season the reader is in, and the entry he is on when the
        # address names one of its own seasons -- the one the switcher above the title moved him to
        # -- with the newest entry standing in otherwise, because that is the season every other tab
        # opens on. The summary describes the book in view (its records, and the name the page is
        # titled with); these describe the reader.
        current = next((item for item in history if item["league_id"] == str(league_id)), None)
        if current is None and history:
            current = history[0]
        current_league_id = str(current["league_id"]) if current else str(league_id)
        current_season = str(current["season"]) if current else str(summary["season"])
        current_league_name = str((current or {}).get("name") or summary["league"].get("name", ""))
        save_connection(username, manager_id, current_league_id, history_id)
        return render_template("history.html", summary=summary, username=username,
                               manager_id=manager_id, season=current_season,
                               current_league_id=current_league_id,
                               switch_page="history",
                               season_options=history, history_league_id=history_id,
                               current_league_name=current_league_name,
                               # The leagues offered are the ones the reader is in this season, the
                               # way every other page offers them, rather than the older season he
                               # has moved the board back to read.
                               league_options=league_switcher_data(
                                   username, manager_id,
                                   str(history[0]["season"]) if history else current_season,
                                   current_league_id))

    @app.get("/league/<league_id>/pick")
    def pick_detail(league_id: str):
        """What became of one traded draft pick, for the Trades page to open on a click.

        A pick is named by its draft year, its round and the roster it started with, which is
        what Sleeper keeps and what survives every hand-off; the answer says where it went, who
        held it at the draft, and what was taken with it -- or that the records cannot separate
        it from a team's other picks in that round.

        The page a pick is clicked on can be an older season -- a 2021 trade can move a 2023
        pick -- so the trace is anchored on the newest season of the chain, not on the season in
        the address. Read from an older season Sleeper's own chain stops there, and a draft held
        since would look like one that has not happened.
        """
        season = request.args.get("season", "").strip()
        raw_round = request.args.get("round", "").strip()
        raw_origin = request.args.get("origin", "").strip()
        if not season.isdigit() or not raw_round.isdigit():
            return jsonify(error="A pick needs a season and a round."), 400
        try:
            origin = int(raw_origin)
        except (TypeError, ValueError):
            origin = None
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        _, history = season_history_for(league_id, username, manager_id)
        root = str(history[0]["league_id"]) if history else league_id
        try:
            detail = draft_picks.trace(root, season, int(raw_round), origin)
        except SleeperError as exc:
            return jsonify(error=str(exc)), 503
        response = jsonify(detail)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/league/<league_id>/ask")
    def ask(league_id: str):
        """Ask AI: a question about this league, answered from this league's own records.

        The page carries no data of its own -- it asks the route below, which reads the league
        and hands back an answer -- so opening it is as cheap as opening any other page and it
        can be linked to from all of them.
        """
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        try:
            dashboard = get_league_dashboard(league_id)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc)), 400
        league = dashboard["league"]
        save_connection(username, manager_id, league_id, history_id)
        seasons = sorted(str(item["season"]) for item in history) or [str(league.get("season") or "")]
        return render_template(
            "ask.html", username=username, manager_id=manager_id,
            league_name=str(league.get("name") or ""),
            current_league_id=league["league_id"],
            season=str(league.get("season") or ""),
            seasons_label=(seasons[0] if len(seasons) == 1 else f"{seasons[0]}\u2013{seasons[-1]}"),
            switch_page="ask",
            season_options=history, history_league_id=history_id,
            current_league_name=str(league.get("name") or ""),
            league_options=league_switcher_data(
                username, manager_id,
                history[0]["season"] if history else str(league.get("season") or ""),
                league["league_id"]),
            ai_ready=ai_available(), ai_model=AI_MODEL,
            key_paths=[str(path) for path in ai_key_paths()],
        )

    @app.post("/league/<league_id>/ask")
    def ask_question(league_id: str):
        """Answer one question about one league, as JSON.

        A missing key comes back as an ordinary answer that cannot be written rather than as a
        server error, because it is something the page can explain to whoever asked.
        """
        username = request.values.get("username", "").strip() or session.get("username", "")
        manager_id = request.values.get("manager_id", "").strip() or session.get("manager_id", "")
        question = request.values.get("question", "")
        # A question about a league is answered from every season it has played, and the tab can
        # be open on an older one, so the tools are anchored on the newest season of the chain.
        _, history = season_history_for(league_id, username, manager_id)
        root = str(history[0]["league_id"]) if history else league_id
        try:
            result = ask_ai.answer(root, question,
                                   reader={"username": username, "manager_id": manager_id})
        except AiUnavailable as exc:
            result = {"ok": False, "error": str(exc), "steps": [], "seconds": 0.0}
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/league/<league_id>/compare")
    def compare(league_id: str):
        """The old Compare address, which is now the panel on the Pace board.

        Two names were the whole of what the page took, and the board takes them the same way,
        so this address still lands on the comparison it names -- the board rather than a page,
        with the two players read into the panel above the table. Nothing links here any more;
        the redirect is for an address already pasted into a league chat before it was folded
        in, so an old link still reaches the figures it was shared for.
        """
        carried = {key: value for key, value in request.args.items() if key != "league_id"}
        return redirect(url_for("pace", league_id=league_id, **carried))

    @app.get("/league/<league_id>/players")
    def league_players(league_id: str):
        """The names a search box in this league completes to, as JSON.

        A compare box asks for this the first time one of them is used: the set is the players
        this league has actually held, so a name completed from it is one the lookup behind the
        box can answer for -- and a reader who never types in one of those boxes fetches none of
        it. Ordered by what each player is on pace for, so the suggestion is the name this league
        would answer about first.
        """
        response = jsonify(names=league_tools.player_names(league_id))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/league/<league_id>/pace")
    def pace(league_id: str):
        """The Pace board: what every player this league has is on pace for, by any stat.

        This is the ranking Ask AI answers a pace question with, given a page of its own: both
        read :func:`services.league_tools.pace_lines`, so a figure here and the same figure in
        an answer are one projection of one set of games. What the page adds is the whole board
        -- every player the league has scored, each with the stats his own position is measured
        by -- in a table that can be reordered by whichever of those stats the reader is
        thinking about, with the column it is reordered by and the position chip it is narrowed
        to both read off the address, so a board can be pasted into a league chat and arrive as
        the board it was.

        The Compare panel on it opens on an example -- the two backs at the top of this same
        board, drawn from the read the board is already built from -- because a comparison is
        easier to recognise than to describe, and a page that only offers two empty boxes asks a
        reader to imagine what he would get. An address that names two players, as the boxes
        themselves write one, replaces it.
        """
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        position = request.args.get("position", "").strip().upper()
        # The column and the way round, as a pasted address spells them. Both are handed to the
        # lookup rather than applied to the markup afterwards, so the rows arrive in the order a
        # shared link asked for and the board reads that way before any script has run.
        sort = request.args.get("sort", "").strip()
        direction = request.args.get("dir", "").strip()
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        try:
            dashboard = get_league_dashboard(league_id)
        except SleeperError as exc:
            return render_template("index.html", error=str(exc)), 400
        league = dashboard["league"]
        # The read is anchored on the newest season of the chain -- the same anchoring an Ask AI
        # question uses -- and told which season to project, so the page opened from an older
        # season projects that season and the switcher reads what it says.
        root = str(history[0]["league_id"]) if history else league_id
        board = get_pace_board(root, season=str(league.get("season") or ""), position=position,
                               sort=sort, direction=direction)
        # Two players' paces, asked for from the panel this board is read with -- beside it where
        # the board is one position's, above it where the board is every position's. The
        # comparison used to be a page of its own; it is now folded into this one, and read from
        # the same lookup the old page and Ask AI drew, so the panel, an answer and a card cannot
        # disagree. Two
        # names, each typed by itself: one alone is not a comparison, so it is shown back in the
        # box that asked for it rather than answered as though it were half of one.
        first = request.args.get("player", "").strip()
        second = request.args.get("other", "").strip()
        # Nothing asked for is not nothing to show: the panel opens on the two backs at the top of
        # this same board, so a reader who has never asked for a comparison sees one -- two names
        # already in the boxes and the card under them -- rather than two empty boxes and a
        # button. The pair comes from the board's own read, so the example costs no second read
        # of the season; the panel says that it is an example, and writing over either name asks
        # for a comparison of the reader's own.
        example = not (first and second)
        if example:
            first, second = (list(board.get("example") or ["", ""]) + ["", ""])[:2]
        comparison = (league_tools.compare_paces(root, first, second,
                                                 season=str(league.get("season") or ""))
                      if first and second else None)
        save_connection(username, manager_id, league_id, history_id)
        seasons = sorted(str(item["season"]) for item in history) or [str(league.get("season") or "")]
        # What the switches to another season or another league carry: the board in view, sort and
        # all. Its own opening order is left unsaid, which is how this app spells a default.
        switch_sort = "" if board["opening"] else board["sort"]
        switch_dir = "" if board["opening"] else board["direction"]
        return render_template(
            "pace.html", username=username, manager_id=manager_id,
            league_name=str(league.get("name") or ""),
            current_league_id=league["league_id"],
            current_league_name=str(league.get("name") or ""),
            season=str(league.get("season") or ""),
            seasons_label=(seasons[0] if len(seasons) == 1 else f"{seasons[0]}\u2013{seasons[-1]}"),
            switch_page="pace", switch_position=position,
            switch_sort=switch_sort, switch_dir=switch_dir,
            switch_player=first, switch_other=second,
            season_options=history, history_league_id=history_id,
            league_options=league_switcher_data(
                username, manager_id,
                history[0]["season"] if history else str(league.get("season") or ""),
                league["league_id"]),
            board=board, first=first, second=second, comparison=comparison,
            comparison_is_example=bool(example and comparison and comparison.get("compared")),
        )

    @app.get("/league/<league_id>/player-data")
    def player_data(league_id: str):
        username = request.args.get("username", "").strip() or session.get("username", "")
        manager_id = request.args.get("manager_id", "").strip() or session.get("manager_id", "")
        # Season-to-date is one set of numbers shown two ways: grouped by roster (team
        # `team=1`) or by position, like the weekly page. The position view is the plain
        # `seasonal=1`, so a link to it reads as "this season" on its own.
        team_view = request.args.get("team") == "1"
        seasonal = team_view or request.args.get("seasonal") == "1"
        drop_lowest = seasonal and request.args.get("drop_lowest") == "1"
        drop_highest = seasonal and request.args.get("drop_highest") == "1"
        history_id, history = season_history_for(league_id, username, manager_id)
        session["season_history_league_id"] = history_id
        raw_week = request.args.get("week")
        try:
            week = int(raw_week) if raw_week and raw_week.isdigit() else None
            summary = get_player_data(league_id, username, manager_id, week, seasonal,
                                      drop_lowest, drop_highest)
        except (SleeperError, ValueError) as exc:
            return render_template("index.html", error=str(exc)), 400
        session.update(league_id=league_id, username=username, manager_id=manager_id)
        save_connection(username, manager_id, league_id, history_id)
        return render_template("player_data.html", summary=summary, team_view=team_view,
                               username=username, manager_id=manager_id,
                               season=str(summary["league"].get("season", "")),
                               current_league_id=summary["league"]["league_id"],
                               switch_page="player_data", switch_week=summary.get("week"),
                               switch_player_seasonal=seasonal,
                               switch_player_team=team_view,
                               switch_drop_lowest=drop_lowest,
                               switch_drop_highest=drop_highest,
                               season_options=history, history_league_id=history_id,
                               current_league_name=str(summary["league"].get("name", "")),
                               league_options=league_switcher_data(
                                   username, manager_id,
                                   history[0]["season"] if history else str(summary["league"].get("season", "")),
                                   summary["league"]["league_id"]))

    return app
