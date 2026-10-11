"""Flask application factory and page routes."""

from urllib.parse import quote_plus
from pathlib import Path
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
from services.trades import get_trades_summary
from services import draft_picks
from services import pick_query
from services import rate_limit
from services.history import get_history_summary
from services.preload import preload_status, start_preload
from services.local_settings import clear_connection, load_connection, save_connection
from services import ask_ai
from services.ask_model import MODEL as AI_MODEL, AiUnavailable, available as ai_available
from services.ask_model import key_paths as ai_key_paths


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


def create_app() -> Flask:
    app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    app = Flask(__name__, template_folder=str(app_root / "templates"),
                static_folder=str(app_root / "static"))
    app.config["SECRET_KEY"] = session_secret()
    app.jinja_env.globals["quote_plus"] = quote_plus
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
        "pick_detail": "data",
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
        # The week view opens on slot-level rows; the Broad View toggle switches it to wider
        # position groups. Impact is measured per slot, so it keeps that grouping either way.
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
        """
        response = jsonify(ok=True, service="deepthreat")
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
        # Broad View toggle asks for position groups. Defense and impact are slot views.
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
        save_connection(username, manager_id, league_id, history_id)
        return render_template("history.html", summary=summary, username=username,
                               manager_id=manager_id, season=summary["season"],
                               current_league_id=summary["league"]["league_id"],
                               switch_page="history",
                               season_options=history, history_league_id=history_id,
                               current_league_name=str(summary["league"].get("name", "")),
                               league_options=league_switcher_data(
                                   username, manager_id,
                                   str(summary["league"].get("season", "")),
                                   summary["league"]["league_id"]))

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
