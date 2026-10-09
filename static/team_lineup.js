(() => {
  const lineup = document.querySelector("#team-lineup");
  const bench = document.querySelector("#team-bench");
  if (!lineup || !bench) return;

  const originalSlots = new Map([...lineup.querySelectorAll(".lineup-slot")].map((slot) => [
    slot.dataset.slotLabel,
    slot.querySelector(".team-player-card")?.dataset.playerId || "",
  ]));
  let draggedCard = null;
  let sourceSlot = null;

  const positionFor = (card) => card.dataset.playerPosition || "—";
  const benchGroupFor = (position) => {
    let group = [...bench.querySelectorAll(".bench-group")].find((item) =>
      item.dataset.benchPosition === position
    );
    if (!group) {
      group = document.createElement("div");
      group.className = "bench-group";
      group.dataset.benchPosition = position;
      const heading = document.createElement("h3");
      const badge = document.createElement("span");
      badge.className = "position";
      badge.textContent = position;
      const count = document.createElement("span");
      heading.append(badge, count);
      group.append(heading);
      bench.append(group);
    }
    return group;
  };
  const addToBench = (card) => {
    bench.querySelector(".bench-empty")?.remove();
    benchGroupFor(positionFor(card)).append(card);
  };
  const refreshBench = () => {
    bench.querySelectorAll(".bench-group").forEach((group) => {
      const cards = group.querySelectorAll(":scope > .team-player-card").length;
      group.querySelector("h3 span:last-child").textContent = String(cards);
      group.hidden = cards === 0;
    });
    if (!bench.querySelector(".bench-group:not([hidden])")) {
      if (!bench.querySelector(".bench-empty")) {
        const empty = document.createElement("div");
        empty.className = "bench-empty";
        empty.textContent = "No players on the bench. Drop a starter here to sit them.";
        bench.append(empty);
      }
    } else {
      bench.querySelector(".bench-empty")?.remove();
    }
  };
  const placeholder = () => {
    const empty = document.createElement("div");
    empty.className = "empty-lineup-slot";
    empty.textContent = "Open";
    return empty;
  };
  const accepts = (slot, card) => slot.dataset.eligible.split(",").includes(positionFor(card));
  const clearDropStyles = () => document.querySelectorAll(".drop-target,.drop-invalid").forEach((el) => {
    el.classList.remove("drop-target", "drop-invalid");
  });

  document.addEventListener("dragstart", (event) => {
    const card = event.target.closest(".team-player-card");
    if (!card) return;
    draggedCard = card;
    sourceSlot = card.closest(".lineup-slot");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.playerId);
    requestAnimationFrame(() => card.classList.add("is-dragging"));
  });
  document.addEventListener("dragend", () => {
    draggedCard?.classList.remove("is-dragging");
    draggedCard = null;
    sourceSlot = null;
    clearDropStyles();
  });

  lineup.addEventListener("dragover", (event) => {
    const slot = event.target.closest(".lineup-slot");
    if (!slot || !draggedCard) return;
    const occupant = slot.querySelector(".team-player-card");
    const canSwap = !occupant || !sourceSlot || accepts(sourceSlot, occupant);
    if (accepts(slot, draggedCard) && canSwap) {
      event.preventDefault();
      clearDropStyles();
      slot.classList.add("drop-target");
      event.dataTransfer.dropEffect = "move";
    } else {
      slot.classList.add("drop-invalid");
      event.dataTransfer.dropEffect = "none";
    }
  });
  lineup.addEventListener("drop", (event) => {
    const target = event.target.closest(".lineup-slot");
    if (!target || !draggedCard || !accepts(target, draggedCard)) return;
    const content = target.querySelector(".slot-content");
    const occupant = content.querySelector(".team-player-card");
    if (occupant && sourceSlot && !accepts(sourceSlot, occupant)) return;
    event.preventDefault();
    if (occupant) {
      if (sourceSlot) sourceSlot.querySelector(".slot-content").append(occupant);
      else addToBench(occupant);
    }
    if (sourceSlot && sourceSlot !== target && !sourceSlot.querySelector(".team-player-card")) {
      sourceSlot.querySelector(".slot-content").append(placeholder());
    }
    content.querySelector(".empty-lineup-slot")?.remove();
    content.append(draggedCard);
    refreshBench();
    clearDropStyles();
  });

  bench.addEventListener("dragover", (event) => {
    if (!draggedCard) return;
    event.preventDefault();
    clearDropStyles();
    bench.classList.add("drop-target");
    event.dataTransfer.dropEffect = "move";
  });
  bench.addEventListener("drop", (event) => {
    if (!draggedCard || !sourceSlot) return;
    event.preventDefault();
    addToBench(draggedCard);
    sourceSlot.querySelector(".slot-content").append(placeholder());
    refreshBench();
    clearDropStyles();
  });

  document.querySelector("[data-lineup-reset]")?.addEventListener("click", () => {
    lineup.querySelectorAll(".lineup-slot").forEach((slot) => {
      const content = slot.querySelector(".slot-content");
      const card = content.querySelector(".team-player-card");
      if (card) addToBench(card);
      content.replaceChildren(placeholder());
    });
    for (const [label, playerId] of originalSlots) {
      if (!playerId) continue;
      const slot = [...lineup.querySelectorAll(".lineup-slot")].find((item) => item.dataset.slotLabel === label);
      const card = [...document.querySelectorAll(".team-player-card")].find((item) => item.dataset.playerId === playerId);
      if (!slot || !card) continue;
      slot.querySelector(".slot-content").replaceChildren(card);
    }
    refreshBench();
  });
  refreshBench();
})();
