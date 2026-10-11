/* Opening a traded pick on the Trades page: the player it became, who spent it, and where it
   went before that.

   A pick is looked up rather than known: its year, its round and the roster it started with are
   what Sleeper records, and the chain of hand-offs has to be read out of the trades themselves.
   So the panel is filled in after a click, and the answer it shows is the same one Ask AI reads
   -- the page and the answer come from one piece of code, so they cannot disagree.

   One panel is open at a time, and clicking the same pick again closes it, because these are
   read next to the trade they belong to rather than instead of it.

   Every value that reaches the page is put there as text, never as markup: a team name is a
   name. */

(function () {
  let openPanel = null;
  let openButton = null;

  function line(panel, className, text) {
    const element = document.createElement('div');
    element.className = className;
    element.textContent = text;
    panel.appendChild(element);
    return element;
  }

  function close() {
    if (openPanel) {
      openPanel.hidden = true;
      openPanel.textContent = '';
    }
    if (openButton) {
      openButton.setAttribute('aria-expanded', 'false');
    }
    openPanel = null;
    openButton = null;
  }

  function render(panel, detail) {
    panel.textContent = '';
    line(panel, 'pick-trace-pick', detail.pick || 'Pick');

    const taken = detail.drafted_with_it || [];
    if (taken.length === 1) {
      const one = taken[0];
      const place = one.slot_label ? 'Pick ' + one.slot_label : 'Pick';
      const overall = one.pick_no ? ' (overall ' + one.pick_no + ')' : '';
      line(panel, 'pick-trace-player', place + overall + ' — ' + one.player
        + (one.position ? ' (' + one.position + ')' : ''));
      if (detail.current_holder) {
        line(panel, 'pick-trace-note', 'Drafted by ' + detail.current_holder + '.');
      }
    } else if (taken.length > 1) {
      line(panel, 'pick-trace-player', 'Held by ' + (detail.current_holder || 'a team')
        + ' at the draft, which had ' + taken.length + ' picks in that round:');
      const list = document.createElement('ul');
      list.className = 'pick-trace-list';
      taken.forEach(function (item) {
        const entry = document.createElement('li');
        entry.textContent = (item.slot_label ? item.slot_label + ' · ' : '')
          + item.player + (item.position ? ' (' + item.position + ')' : '');
        list.appendChild(entry);
      });
      panel.appendChild(list);
    } else {
      line(panel, 'pick-trace-note', detail.note || 'No player has been taken with this pick.');
    }

    const handoffs = detail.handoffs || [];
    if (handoffs.length) {
      // With a move missing from Sleeper's feed the chain does not run hand to hand, so each
      // readable move names both of its ends rather than only where it landed -- otherwise the
      // route would appear to skip a team.
      const chain = handoffs.map(function (step) {
        const at = ' (' + step.season + ' Wk' + step.week + ')';
        return detail.unread_moves ? step.from + ' → ' + step.to + at : step.to + at;
      });
      line(panel, 'pick-trace-chain',
        'Started with ' + (detail.original_team || '—') + ' → ' + chain.join(' → '));
    } else if (taken.length) {
      line(panel, 'pick-trace-chain', 'Never traded — ' + (detail.original_team || '') + ' kept it.');
    }

    // The player is what the pick became; a caveat about a move missing from Sleeper's feed is
    // shown beside him rather than instead of him, because both are true at once.
    if (detail.caveat) {
      line(panel, 'pick-trace-caveat', detail.caveat);
    }

    if (taken.length > 1) {
      line(panel, 'pick-trace-caveat',
        'Sleeper files a traded pick by its year, round and the team it started with, never by a '
        + 'place in the draft — and this one could not be placed in that round\u2019s order, so '
        + 'which of those rookies it became cannot be told apart.');
    }

    if (detail.multi_draft) {
      line(panel, 'pick-trace-caveat',
        'This season held more than one draft and a traded pick is not tied to one of them, so '
        + 'the round is read from the draft Sleeper lists first for that year.');
    }

    if (detail.unread_moves) {
      line(panel, 'pick-trace-caveat',
        'Sleeper\u2019s trade feed is missing at least one earlier move of this pick, so the route '
        + 'above starts later than the pick did \u2014 it began with '
        + (detail.original_team || 'its original team') + '.');
    }
  }

  function load(button, panel) {
    const url = button.getAttribute('data-pick-url');
    const season = button.getAttribute('data-pick-season') || '';
    const round = button.getAttribute('data-pick-round') || '';
    const origin = button.getAttribute('data-pick-origin') || '';
    if (!url || !season || !round) {
      return;
    }
    panel.hidden = false;
    panel.textContent = '';
    line(panel, 'pick-trace-note', 'Reading what this pick became…');
    const query = '?season=' + encodeURIComponent(season) + '&round=' + encodeURIComponent(round)
      + '&origin=' + encodeURIComponent(origin);
    fetch(url + query, { headers: { 'Accept': 'application/json' } })
      .then(function (response) { return response.json(); })
      .then(function (detail) {
        if (detail && detail.error) {
          render(panel, { pick: '', note: detail.error, handoffs: [], drafted_with_it: [] });
          return;
        }
        render(panel, detail || {});
      })
      .catch(function () {
        render(panel, { pick: '', note: 'That pick could not be read from Sleeper. Try again.',
                        handoffs: [], drafted_with_it: [] });
      });
  }

  document.addEventListener('click', function (event) {
    const button = event.target.closest ? event.target.closest('.pick-open') : null;
    if (!button) {
      return;
    }
    event.preventDefault();
    const panel = button.parentElement
      ? button.parentElement.querySelector('.pick-trace') : null;
    if (!panel) {
      return;
    }
    if (openButton === button) {
      close();
      return;
    }
    close();
    openButton = button;
    openPanel = panel;
    button.setAttribute('aria-expanded', 'true');
    load(button, panel);
  });
})();
