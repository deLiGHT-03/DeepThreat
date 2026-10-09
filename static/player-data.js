document.addEventListener("DOMContentLoaded", () => {
  // Hovering a team name lights that team's players, the way your own rows are lit.
  const HOVER_CLASS = "player-data-team-hover";
  // Inside a quoted attribute selector only quotes and backslashes need escaping, and a
  // CSS.escape would mangle a name that starts with a digit.
  const quoted = (value) => value.replace(/["\\]/g, "\\$&");
  let hoveredTeam = "";

  function paintTeam(key, on) {
    document.querySelectorAll(`tr[data-team="${quoted(key)}"]`).forEach((row) => {
      // Your own rows keep your own highlight rather than gaining a second one.
      if (row.closest(".my-matchup") || row.classList.contains("player-data-you-row")) return;
      row.classList.toggle(HOVER_CLASS, on);
    });
  }

  document.addEventListener("mouseover", (event) => {
    const name = event.target.closest?.(".player-data-team-name");
    const key = name ? name.dataset.teamName || "" : "";
    if (key === hoveredTeam) return;
    if (hoveredTeam) paintTeam(hoveredTeam, false);
    hoveredTeam = key;
    if (key) paintTeam(key, true);
  });

  // Leaving the window fires no mouseover to clear the highlight with.
  document.addEventListener("mouseout", (event) => {
    if (event.relatedTarget || !hoveredTeam) return;
    paintTeam(hoveredTeam, false);
    hoveredTeam = "";
  });

  document.querySelectorAll("[data-player-sort]").forEach((table) => {
    let activeKey = "ppg";
    let descending = true;

    table.querySelectorAll("[data-sort-key]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.sortKey;
        if (key === activeKey) {
          descending = !descending;
        } else {
          activeKey = key;
          descending = button.dataset.sortDirection === "desc";
        }

        const rows = [...table.tBodies[0].rows];
        // Score columns hold numbers and must sort numerically; a missing value
        // (a player with no games played) always sorts below a real one.
        const isScore = key === "ppg" || key === "voas";
        const score = (value) => {
          const parsed = Number(value);
          return value === "" || !Number.isFinite(parsed) ? Number.NEGATIVE_INFINITY : parsed;
        };
        rows.sort((a, b) => {
          const aValue = a.dataset[key] || "";
          const bValue = b.dataset[key] || "";
          if (!isScore) {
            return (descending ? -1 : 1) * aValue.localeCompare(bValue, undefined, { sensitivity: "base" });
          }
          const aScore = score(aValue);
          const bScore = score(bValue);
          if (aScore === bScore) return 0;
          return (descending ? -1 : 1) * (aScore < bScore ? -1 : 1);
        });
        rows.forEach((row) => table.tBodies[0].append(row));

        table.querySelectorAll("thead th").forEach((header) => {
          const control = header.querySelector("[data-sort-key]");
          if (!control) return;
          const selected = control.dataset.sortKey === activeKey;
          const direction = selected ? (descending ? "descending" : "ascending") : "none";
          header.setAttribute("aria-sort", direction);
          const indicator = control.querySelector("span");
          if (indicator) indicator.textContent = selected ? (descending ? "↓" : "↑") : "↕";
        });
      });
    });
  });
});
