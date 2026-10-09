(() => {
  const button = document.querySelector("[data-preload]");
  if (!button) return;

  const url = button.dataset.preloadUrl;
  const label = button.querySelector(".preload-label");
  const POLL_MS = 600;
  const RESET_MS = 6000;

  let timer = null;
  let resetTimer = null;
  let pressed = false;

  const setLabel = (text) => {
    if (label) label.textContent = text;
  };

  const read = async (method) => {
    try {
      // The reload carries ?refresh=1, so join with the separator this URL actually needs.
      const target = method === "POST" ? `${url}${url.includes("?") ? "&" : "?"}refresh=1` : url;
      const response = await fetch(target, { method, cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch {
      return { status: "error", error: "The app could not be reached.", progress: 0 };
    }
  };

  const showLoading = (state) => {
    window.clearTimeout(resetTimer);
    button.dataset.state = "loading";
    button.disabled = true;
    setLabel(`Loading ${Math.round((state.progress || 0) * 100)}%…`);
    button.title = state.detail ? `Loading ${state.detail}` : "Loading all league data";
  };

  const showReady = (state, reload) => {
    button.dataset.state = "ready";
    button.disabled = false;
    setLabel(state.seconds ? `Data ready · ${state.seconds}s` : "Data ready");
    button.title = `All league data is loaded (${state.entries} responses). Press to reload it.`;
    window.clearTimeout(resetTimer);
    resetTimer = window.setTimeout(() => setLabel("Reload Data"), RESET_MS);
    if (reload) window.location.reload();
  };

  const showError = (state) => {
    button.dataset.state = "error";
    button.disabled = false;
    setLabel("Retry Loading");
    button.title = state.error || "The league data could not be loaded.";
  };

  const watch = () => {
    window.clearInterval(timer);
    timer = window.setInterval(async () => {
      const state = await read("GET");
      if (state.status === "loading") {
        showLoading(state);
        return;
      }
      window.clearInterval(timer);
      if (state.status === "ready") showReady(state, pressed);
      else showError(state);
    }, POLL_MS);
  };

  button.addEventListener("click", async () => {
    pressed = true;
    showLoading({ progress: 0 });
    const state = await read("POST");
    if (state.status === "loading") watch();
    else if (state.status === "ready") showReady(state, true);
    else showError(state);
  });

  // A league that has never been loaded starts loading on its own, so switching leagues
  // (or opening the app) leaves every later page reading from memory instead of Sleeper.
  read("GET").then((state) => {
    if (state.status === "loading") {
      showLoading(state);
      watch();
    } else if (state.status === "ready") {
      button.dataset.state = "ready";
      setLabel("Reload Data");
      button.title = `All league data is loaded (${state.entries} responses). Press to reload it.`;
    } else if (state.status === "error") {
      showError(state);
    } else {
      pressed = false;
      showLoading({ progress: 0 });
      read("POST").then((started) => {
        if (started.status === "loading") watch();
        else if (started.status === "ready") showReady(started, false);
        else showError(started);
      });
    }
  });
})();
