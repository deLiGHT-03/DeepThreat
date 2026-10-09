"""Flask application factory and page routes."""

from urllib.parse import quote_plus
from pathlib import Path
import sys

from flask import (Flask, Response, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from services.sleeper import (CDN_SECONDS, MAX_NFL_WEEK, SleeperError, get_cdn_file,
                              get_league_dashboard, get_json, get_league_season_history,
                              get_user, get_user_account_leagues)
from services.matchups import (get_default_week,
                               get_week_matchups, is_week_started)
from services.seasonal import get_seasonal_impact_summary, get_seasonal_summary
from services.player_data import get_player_data
from services.player_card import get_player_card
from services.preload import preload_status, start_preload
from services.local_settings import clear_connection, load_connection, save_connection


def create_app() -> Flask:
    app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    app = Flask(__name__, template_folder=str(app_root / "templates"),
                static_folder=str(app_root / "static"))
    app.config["SECRET_KEY"] = "local-fantasy-football-app"
    app.jinja_env.globals["quote_plus"] = quote_plus
    # The Impact Players page is hidden until its shape is settled. Its routes and data
    # stay live, so this one flag brings the tab back on every page that links to it.
    app.jinja_env.globals["show_impact_players"] = False

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
        return render_template("leagues.html", username=manager.get("username") or username,
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
