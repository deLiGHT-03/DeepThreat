(() => {
  const script = document.currentScript;
  const statusUrl = script?.dataset.statusUrl;
  if (!statusUrl) return;

  window.setInterval(async () => {
    if (![0, 1, 4].includes(new Date().getDay())) return;
    try {
      const response = await fetch(statusUrl, { cache: "no-store" });
      if (!response.ok) return;
      const state = await response.json();
      if (state.started === true) window.location.reload();
    } catch (_) {
      // Keep the projection visible if the schedule check is temporarily unavailable.
    }
  }, 5 * 60 * 1000);
})();
