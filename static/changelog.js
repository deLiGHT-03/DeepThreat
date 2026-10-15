/* The notebook beside the mode switch: opening it, closing it, and remembering that it has been
   read. The list itself is already in the page -- this only shows and hides it -- so there is
   nothing here to fetch and nothing that can fail to load.

   Closing follows the app's own habits: Escape, a click outside, or the button again. */
(() => {
  const storageKey = 'deepthreat-changelog-seen';

  const readSeen = () => {
    try { return localStorage.getItem(storageKey) || ''; } catch (_) { return ''; }
  };
  const writeSeen = (value) => {
    try { localStorage.setItem(storageKey, value); } catch (_) { /* Storage may be disabled. */ }
  };

  document.querySelectorAll('[data-changelog]').forEach((root) => {
    const trigger = root.querySelector('[data-changelog-trigger]');
    const panel = root.querySelector('.changelog-panel');
    const dot = root.querySelector('[data-changelog-dot]');
    // Identifies the newest release, title and all: two can share a date when the second is
    // written the same day as the first, and only the title tells them apart.
    const latest = root.dataset.changelogLatest || '';
    if (!trigger || !panel) return;

    // The panel is anchored under the button and drops towards the bottom of the window, so it is
    // measured against the window before it is shown: the toolbar sits near the top of a page, and
    // a fixed height there either runs off the screen on a laptop or leaves a tall panel half
    // empty on a desktop. Where there is more room above the button than below it, it opens
    // upwards instead -- the notebook is at the bottom of the window as often as at the top.
    const place = () => {
      const rect = trigger.getBoundingClientRect();
      const above = rect.top - 12;
      const below = window.innerHeight - rect.bottom - 12;
      const upwards = below < 320 && above > below;
      panel.classList.toggle('changelog-panel-above', upwards);
      // The room that is there, and not a comfortable height the window may not have: the list
      // scrolls, so a short window gets a short panel rather than one off the edge of the screen.
      panel.style.maxHeight = `${Math.max(140, upwards ? above : below)}px`;
      // A column of prose is as wide as the window can hold and no wider, and the panel is placed
      // after it is that wide rather than from the button alone: the notebook stands at the left
      // end of a narrow toolbar, where a panel right-aligned to it would open off the left edge of
      // the page. So the width is settled first and the panel is then shifted back inside the
      // window, however far left its button is -- which is no shift at all where the button already
      // sits at the window's right, the way it does on a wide page.
      const width = Math.max(240, Math.min(440, window.innerWidth - 24));
      panel.style.width = `${width}px`;
      const right = Math.min(Math.max(rect.right, 12 + width), window.innerWidth - 12);
      panel.style.right = `${Math.round(rect.right - right)}px`;
    };

    const close = (restoreFocus = false) => {
      panel.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
    };

    const open = () => {
      place();
      panel.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      // Reading is the whole point of the panel, so the focus goes into it: a keyboard lands in
      // the list it just opened rather than on whatever was next in the page.
      panel.focus();
      if (dot) dot.hidden = true;
      if (latest) writeSeen(latest);
    };

    trigger.addEventListener('click', () => {
      if (panel.hidden) open();
      else close(true);
    });

    root.querySelectorAll('[data-changelog-close]').forEach((button) => {
      button.addEventListener('click', () => close(true));
    });

    document.addEventListener('pointerdown', (event) => {
      if (!panel.hidden && !root.contains(event.target)) close();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !panel.hidden) close(true);
    });

    // A window resized while the panel is open is the one case the measurement above goes stale.
    window.addEventListener('resize', () => {
      if (!panel.hidden) place();
    });

    // The dot is on the button rather than in the panel so it survives a reader who never opens
    // it -- and it is cleared by opening, not by the dot expiring on its own.
    if (dot && latest && readSeen() !== latest) dot.hidden = false;
  });
})();
