(() => {
  const storageKey = 'deepthreat-skin';
  const skins = new Set(['deepthreat', 'classic', 'arctic', 'ember', 'forest', 'royal', 'copper']);

  function setSkin(name) {
    const skin = skins.has(name) ? name : 'deepthreat';
    if (skin === 'classic') document.body.removeAttribute('data-skin');
    else document.body.dataset.skin = skin;
    try { localStorage.setItem(storageKey, skin); } catch (_) { /* Storage may be disabled. */ }
    document.querySelectorAll('[data-skin-choice]').forEach((button) => {
      button.setAttribute('aria-checked', String(button.dataset.skinChoice === skin));
    });
  }

  let saved = 'deepthreat';
  try { saved = localStorage.getItem(storageKey) || localStorage.getItem('fantasy-desk-skin') || 'deepthreat'; } catch (_) { /* Use DeepThreat without storage. */ }
  if (saved === 'default') saved = 'deepthreat';
  if (saved === 'paper' || saved === 'slate') saved = 'copper';
  setSkin(saved);

  document.querySelectorAll('[data-skin-picker]').forEach((picker) => {
    const trigger = picker.querySelector('.skin-picker-trigger');
    const menu = picker.querySelector('.skin-picker-menu');
    const choices = [...picker.querySelectorAll('[data-skin-choice]')];
    const close = (restoreFocus = false) => {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
    };
    trigger.addEventListener('click', () => {
      const opening = menu.hidden;
      menu.hidden = !opening;
      trigger.setAttribute('aria-expanded', String(opening));
      if (opening) choices.find((button) => button.getAttribute('aria-checked') === 'true')?.focus();
    });
    choices.forEach((button, index) => {
      button.addEventListener('click', () => {
        setSkin(button.dataset.skinChoice);
        close(true);
      });
      button.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          choices[(index + (event.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length].focus();
        } else if (event.key === 'Escape') close(true);
      });
    });
    document.addEventListener('pointerdown', (event) => {
      if (!picker.contains(event.target)) close();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !menu.hidden) close(true);
    });
  });
})();
