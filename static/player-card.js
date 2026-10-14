// Clicking a player's name anywhere in the app opens one card: his headshot, the rate he
// scores at with the games behind it, his NFL schedule for the season, the fantasy points he
// scored in this league each week, and his season total. Any element can opt in by carrying
// `.player-card-target` and `data-player-id`, so a new page does not need new code here.
//
// The card is asked for, not stumbled onto: nothing opens until a name is clicked, so moving
// the pointer across a table of names costs nothing and never leaves a card in the way. One
// request per player is kept for the life of the page, and the card stays put while it is read
// and scrolled, until the reader clicks the name again or clicks away.
(() => {
  "use strict";

  const GAP = 6;
  const MARGIN = 8;

  // Every page that shows player names has the league in its URL.
  const leagueId = (window.location.pathname.match(/^\/league\/([^/]+)\//) || [])[1];
  if (!leagueId) return;

  // The positions a pace table says something about. A kicker and a defense are scored by
  // things this app does not project, so their cards carry no button rather than an empty one.
  const PACE_POSITIONS = new Set(["QB", "RB", "WR", "TE"]);

  const requests = new Map(); // player id -> in-flight or settled card request
  const paces = new Map(); // player id -> in-flight or settled pace request
  let open = null; // the card element on screen
  let anchor = null; // the name it belongs to
  let pending = null; // the name being loaded for

  const isElement = (node) => node instanceof Element;
  const triggerOf = (node) =>
    (isElement(node) ? node.closest(".player-card-target[data-player-id]") : null);
  const insideCard = (node) => isElement(node) && node.closest(".player-card") !== null;

  // The server hands these over already rounded to the one decimal they are printed at, so this
  // only has to print them: a figure the tables behind the card also show is rounded once, in
  // one place, and the two cannot round an exact half-tenth apart.
  const points = (value) =>
    (value === null || value === undefined ? "\u2014" : Number(value).toFixed(1));

  const cardUrl = (playerId) =>
    `/league/${encodeURIComponent(leagueId)}/player/${encodeURIComponent(playerId)}/card`;
  const paceUrl = (playerId) =>
    `/league/${encodeURIComponent(leagueId)}/player/${encodeURIComponent(playerId)}/pace`;

  function load(playerId) {
    const known = requests.get(playerId);
    if (known) return known;
    const request = fetch(cardUrl(playerId), { headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null);
    requests.set(playerId, request);
    // A failure is not an answer: let the next click try again.
    request.then((card) => { if (!card) requests.delete(playerId); });
    return request;
  }

  // A pace the server declined to compute comes back as an error with a sentence in it --
  // "no games to project yet" -- and that sentence is the answer to show. Only a request that
  // never arrived is treated as no answer at all, so a later press asks again.
  function loadPace(playerId) {
    const known = paces.get(playerId);
    if (known) return known;
    const missing = "This player's pace could not be computed.";
    const request = fetch(paceUrl(playerId), { headers: { Accept: "application/json" } })
      .then((response) => response.json().then(
        (body) => (response.ok && body ? body : { error: (body && body.error) || missing }),
        () => (response.ok ? null : { error: missing })))
      .catch(() => null);
    paces.set(playerId, request);
    request.then((pace) => { if (!pace) paces.delete(playerId); });
    return request;
  }

  function initials(name) {
    return name.split(/\s+/).filter(Boolean).slice(0, 2)
      .map((word) => word[0].toUpperCase()).join("") || "?";
  }

  function weekRow(week) {
    const row = document.createElement("div");
    row.className = "player-card-week";

    const label = document.createElement("span");
    label.className = "player-card-week-label";
    label.textContent = `Wk${week.week}`;

    const opponent = document.createElement("span");
    opponent.className = "player-card-opp";
    if (!week.scheduled) {
      opponent.textContent = "\u2014";
    } else if (!week.opponent) {
      opponent.textContent = "BYE";
      row.classList.add("is-bye");
    } else {
      opponent.textContent = `${week.home ? "vs" : "@"} ${week.opponent}`;
    }

    const score = document.createElement("b");
    score.className = week.points === null ? "player-card-points is-empty" : "player-card-points";
    score.textContent = points(week.points);
    // Dim the weeks still to come. A bye is settled rather than upcoming, and so is a week the
    // league has already scored that this player had no part in -- a pool he was not started in,
    // or a playoff round his team was not in -- which is a dash, not a week yet to be played.
    if (week.pending && week.points === null && week.opponent) row.classList.add("is-upcoming");

    row.append(label, opponent, score);
    return row;
  }

  // A pace is one figure per stat, so the table is one value column: the stat, and what a full
  // season of it would read. A whole figure is grouped the way a yardage total is read (4,767),
  // and a decimal one is left as it is, because one decimal is already the whole of it.
  function paceValue(row) {
    const value = Number(row.pace);
    if (!Number.isFinite(value)) return "\u2014";
    const fixed = value.toFixed(row.decimals);
    return row.decimals ? fixed : Number(fixed).toLocaleString("en-US");
  }

  function paceTable(pace) {
    const table = document.createElement("table");
    table.className = "player-card-pace-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    const statHead = document.createElement("th");
    statHead.scope = "col";
    statHead.textContent = "Stat";
    const paceHead = document.createElement("th");
    paceHead.scope = "col";
    paceHead.textContent = `${pace.season_games} GM`;
    headRow.append(statHead, paceHead);
    head.append(headRow);

    const body = document.createElement("tbody");
    pace.rows.forEach((row) => {
      const line = document.createElement("tr");
      const label = document.createElement("th");
      label.scope = "row";
      label.textContent = row.label;
      // The short name a reader scans a box score by, beside the long one he may not know:
      // PASY and RECY are how these columns are headed everywhere else he reads them.
      const abbreviation = document.createElement("small");
      abbreviation.className = "player-card-pace-abbr";
      abbreviation.textContent = row.abbr;
      label.append(abbreviation);
      const value = document.createElement("td");
      value.textContent = paceValue(row);
      // Points per game is already a rate, so its "pace" is the same figure rather than a
      // projection of it. Say so where the number is, because the column does not.
      if (row.rate) value.title = "Already a per-game rate";
      line.append(label, value);
      body.append(line);
    });
    table.append(head, body);
    return table;
  }

  // The button at the foot of a card. Nothing is asked for until it is pressed, because most
  // cards are read and closed without anyone wanting a projection from them.
  function paceSection(card) {
    const section = document.createElement("div");
    section.className = "player-card-pace";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "player-card-pace-button";
    button.textContent = "Generate Pace";
    button.addEventListener("click", () => generatePace(section, button, card));
    section.append(button);
    return section;
  }

  async function generatePace(section, button, card) {
    button.disabled = true;
    button.textContent = "Generating\u2026";
    const pace = await loadPace(card.id);
    const panel = section.closest(".player-card");
    // The card may have been put away while the pace was on its way.
    if (!panel || !section.isConnected) return;
    section.replaceChildren();
    if (pace && !pace.error) {
      const title = document.createElement("div");
      title.className = "player-card-pace-title";
      title.textContent = `${pace.season_games}-Game Pace`;
      const note = document.createElement("div");
      note.className = "player-card-pace-note";
      note.textContent = `From ${pace.games} ${pace.games === 1 ? "game" : "games"} played`;
      section.append(title, paceTable(pace), note);
    } else {
      const note = document.createElement("div");
      note.className = "player-card-pace-note";
      note.textContent = (pace && pace.error) || "This player's pace could not be computed.";
      section.append(note);
    }
    // The table makes the card taller than it was when it was placed, and a panel that grows
    // off the bottom of the window is a panel that cannot be read. The weekly list gives up
    // some of its own room to keep the whole card in view.
    panel.classList.add("has-pace");
    if (anchor) place(panel, anchor);
  }

  function build(card) {
    const panel = document.createElement("div");
    panel.className = "player-card";
    panel.setAttribute("role", "tooltip");

    const head = document.createElement("header");
    head.className = "player-card-head";

    const image = document.createElement("img");
    image.className = "player-card-headshot";
    image.alt = "";
    if (card.headshot_url) {
      image.dataset.avatarFallbacks = (card.headshot_fallbacks || []).join("|");
      image.src = card.headshot_url;
    } else {
      image.hidden = true;
    }
    const placeholder = document.createElement("span");
    placeholder.className = "team-logo-placeholder player-card-headshot-placeholder";
    // A defense has no portrait to fall back on, so its team abbreviation stands in.
    placeholder.textContent = card.position === "DEF" ? card.nfl_team : initials(card.name);
    placeholder.hidden = Boolean(card.headshot_url);

    const identity = document.createElement("div");
    identity.className = "player-card-identity";
    const name = document.createElement("strong");
    name.className = "player-card-name";
    name.textContent = card.name;
    const meta = document.createElement("span");
    meta.className = "player-card-meta";
    const position = document.createElement("span");
    position.className = `position pos-${String(card.position).toLowerCase().replace("/", "-")}`;
    position.textContent = card.position;
    const nflTeam = document.createElement("span");
    nflTeam.textContent = card.nfl_team;
    meta.append(position, nflTeam);
    identity.append(name, meta);
    if (card.fantasy_team) {
      const fantasyTeam = document.createElement("span");
      fantasyTeam.className = "player-card-team";
      fantasyTeam.textContent = card.fantasy_team;
      identity.append(fantasyTeam);
    }
    // The rate and the games behind it, in the corner opposite the name: the number a reader
    // compares players by first, the evidence for it underneath and quieter.
    const scoring = document.createElement("div");
    scoring.className = "player-card-scoring";
    const rateLabel = document.createElement("span");
    rateLabel.className = "player-card-scoring-label";
    rateLabel.textContent = "PPG";
    const rate = document.createElement("b");
    rate.className = "player-card-ppg";
    rate.textContent = points(card.points_per_game);
    if (card.points_per_game === null || card.points_per_game === undefined) {
      rate.classList.add("is-empty");
    }
    const played = document.createElement("small");
    played.className = "player-card-games";
    played.textContent = `${card.games} GP`;
    scoring.append(rateLabel, rate, played);
    head.append(image, placeholder, identity, scoring);

    const weeks = document.createElement("div");
    weeks.className = "player-card-weeks";
    card.weeks.forEach((week) => weeks.append(weekRow(week)));

    const total = document.createElement("footer");
    total.className = "player-card-total";
    const totalLabel = document.createElement("span");
    totalLabel.textContent = "SEASON TOTAL";
    const totalValue = document.createElement("b");
    totalValue.textContent = `${points(card.total_points)} pts`;
    total.append(totalLabel, totalValue);
    // The rate and its games have moved up to the corner; a player who has not appeared yet has
    // no rate at all, so say so in the space the season total leaves.
    if (!card.games) {
      const detail = document.createElement("span");
      detail.className = "player-card-games";
      detail.textContent = "No games recorded yet";
      total.append(detail);
    }

    panel.append(head, weeks, total);
    if (PACE_POSITIONS.has(String(card.position).toUpperCase())) {
      panel.append(paceSection(card));
    }
    return panel;
  }

  function unavailable() {
    const panel = document.createElement("div");
    panel.className = "player-card";
    panel.setAttribute("role", "tooltip");
    const note = document.createElement("div");
    note.className = "player-card-note";
    note.textContent = "This player's card could not be loaded.";
    panel.append(note);
    return panel;
  }

  function place(panel, trigger) {
    const name = trigger.getBoundingClientRect();
    const box = panel.getBoundingClientRect();
    let top = name.bottom + GAP;
    if (top + box.height > window.innerHeight - MARGIN
        && name.top - box.height - GAP >= MARGIN) {
      top = name.top - box.height - GAP;
    }
    const centred = name.left + name.width / 2 - box.width / 2;
    const left = Math.min(Math.max(MARGIN, centred),
                          Math.max(MARGIN, window.innerWidth - box.width - MARGIN));
    panel.style.top = `${Math.round(Math.max(MARGIN, top))}px`;
    panel.style.left = `${Math.round(left)}px`;
  }

  /**
   * Put the card's rate and season total on the same right edge as its weekly scores.
   *
   * The week list hands its scrollbar a slice of its own column, so its scores end further from
   * the edge than the header and the footer do. Handing those the same inset lines every value
   * up, and it has to be measured: how wide a scrollbar is belongs to the platform, and where it
   * floats over the content instead there is nothing to take.
   */
  function matchScrollbarInset(panel) {
    const weeks = panel.querySelector(".player-card-weeks");
    const gutter = weeks ? weeks.offsetWidth - weeks.clientWidth : 0;
    panel.style.setProperty("--player-card-gutter", `${gutter}px`);
  }

  function show(panel, trigger) {
    if (open) open.remove();
    document.body.append(panel);
    // After it is in the document, so the list has given its scrollbar its room already.
    matchScrollbarInset(panel);
    place(panel, trigger);
    open = panel;
    anchor = trigger;
    // One frame so the card is laid out before it fades in.
    requestAnimationFrame(() => panel.classList.add("is-open"));
  }

  function hide() {
    if (open) open.remove();
    open = null;
    anchor = null;
    pending = null;
  }

  async function openFor(trigger) {
    pending = trigger;
    const card = await load(trigger.dataset.playerId);
    if (pending !== trigger || !trigger.isConnected) return;
    // The page can move while the card is on its way: a click near the edge of the window scrolls
    // its own name into view first, and that is a real scroll event. The card is only shown if the
    // name it belongs to is still on screen, so a reader who has scrolled away is left alone.
    const box = trigger.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) {
      pending = null;
      return;
    }
    show(card ? build(card) : unavailable(), trigger);
  }

  // A name opens its card on a click, the same name closes it again, and a click anywhere else
  // puts it away. Nothing here answers the pointer merely passing over a name.
  document.addEventListener("click", (event) => {
    if (insideCard(event.target)) return; // reading the card is not asking for another
    const trigger = triggerOf(event.target);
    if (!trigger) {
      hide();
    } else if (anchor === trigger && open) {
      hide();
    } else {
      openFor(trigger);
    }
  });

  // The card is pinned to the viewport, so anything that moves the page closes it. Only a card
  // that is actually on screen, though: one still being fetched has nothing to move, and the
  // click that asked for it may itself have scrolled its name into view - it is left to land.
  window.addEventListener("scroll", (event) => {
    if (insideCard(event.target)) return; // that is the card's own week list scrolling
    if (open) hide();
  }, true);
  window.addEventListener("resize", () => hide());
  // A press is only a request to dismiss the card when it is the primary button and it lands
  // on neither a name nor the card: pressing the wheel starts autoscroll, pressing the card is
  // someone reading it, and pressing a name is the click that decides what happens next.
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || insideCard(event.target) || triggerOf(event.target)) return;
    hide();
  }, true);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });
})();
