/* Ask AI: send the question, show the answer, and keep the lookups behind a click.

   The answer arrives as plain text from the server, and every piece of it is put on the page as
   text rather than markup -- the answer quotes a league's player and team names, and a name is
   never a reason to render anything. Lines the model wrote as list items are rebuilt as list
   items, a run of cell lines is rebuilt as a table, and everything else is a paragraph.

   Two things in that text get the shape the rest of the app gives them: a position becomes the
   same badge a roster row wears, and a draft pick the gold chip the Trades page uses. Both are
   recognized only as whole tokens of the answer's own text, and both are still set as text. */

(function () {
  const form = document.getElementById('ask-form');
  if (!form) {
    return;
  }
  const input = document.getElementById('ask-question');
  const button = document.getElementById('ask-submit');
  const panel = document.getElementById('ask-answer');
  const body = document.getElementById('ask-answer-body');
  const meta = document.getElementById('ask-meta');
  const trace = document.getElementById('ask-trace');
  const steps = document.getElementById('ask-steps');

  document.querySelectorAll('.ask-sample').forEach(function (sample) {
    sample.addEventListener('click', function () {
      input.value = sample.textContent.trim();
      input.focus();
    });
  });

  // A row of cells is a line with a "|" in it, and a table is two or more of those lines in a
  // row. The first of them is the column names when the line under it is the dashes markdown
  // tables use; without that line the rows are simply rows. A lone line with a pipe in it is
  // prose that happens to contain a pipe, so it stays a paragraph.
  function splitCells(line) {
    let text = line.trim();
    if (text.charAt(0) === '|') {
      text = text.slice(1);
    }
    if (text.charAt(text.length - 1) === '|') {
      text = text.slice(0, -1);
    }
    return text.split('|').map(function (cell) { return cell.trim(); });
  }

  const DASHES = /^:?-{2,}:?$/;
  const NUMBERISH = /^-?[\d][\d,.]*(%|\$)?$/;

  //: The mark a table uses for a stat a player's position is not measured by. It is the absence
  //: of a figure rather than a word, so it does not turn a column of figures into a column of
  //: text -- but it is still set quieter than the figures beside it.
  const BLANK = /^[\u2014\u2013]$/;

  //: The positions every other page puts in a badge. The short codes only: a position written
  //: out long is prose, and a single letter is how a name is spelled ("D.K. Metcalf").
  const POSITION_TOKEN = /\b(QB|RB|WR|TE|DEF|DST)\b/g;

  //: A draft pick, in the forms a manager writes one: the year and the round ("2025 1st",
  //: "2022 2nd Rd"), a round with its place in it ("3rd Rd (3.10)"), a round on its own
  //: ("4th round pick"), and a bare place ("3.04") -- which is the one form that needs care,
  //: so it is the only alternative with a capture group, and a caller can say whether the place
  //: it just read is a pick or a figure. That is what stops a week's 27.66 points wearing a
  //: pick's gold.
  const PICK_TOKEN = new RegExp([
    '\\b(?:19|20)\\d{2}[\\s-]+(?:1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)(?:[\\s-]+(?:Rd|round))?',
    '\\b(?:1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)[\\s-]+(?:Rd|round)(?:[\\s-]+pick)?\\s*\\(\\s*\\d{1,2}\\.\\d{2}\\s*\\)',
    '\\b(?:1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)[\\s-]+(?:Rd|round)(?:[\\s-]+pick)?',
    '\\b(\\d{1,2}\\.\\d{2})\\b',
  ].join('|'), 'gi');

  //: What makes a bare place a pick rather than a number: a pick word beside it.
  const PICK_WORDS = /(pick|draft|round|Rd|overall)/i;

  function isCellLine(line) {
    return line.indexOf('|') !== -1;
  }

  //: A column whose heading names a pick or a place, where a cell like "3.04" is a pick rather
  //: than a figure.
  const PICK_COLUMN = /pick|place|draft|round/i;

  //: Something that reads only as a place in a draft round. It is a number to the eye, so a pick
  //: column has to be told it is not one, or a table of picks would line up as figures.
  const SLOTISH = /^\d{1,2}\.\d{2}$/;

  // Figures are lined up so a column of them can be read down; a column of names stays left.
  function numbersIn(head, rows) {
    const width = head ? head.length : (rows[0] ? rows[0].length : 0);
    const flags = [];
    for (let column = 0; column < width; column += 1) {
      const named = PICK_COLUMN.test(head ? (head[column] || '') : '');
      flags.push(rows.length > 0 && rows.every(function (cells) {
        const cell = (cells[column] || '').trim();
        if (cell === '') {
          return false;
        }
        if (BLANK.test(cell)) {
          return true;
        }
        if (!NUMBERISH.test(cell)) {
          return false;
        }
        return !(named && SLOTISH.test(cell));
      }));
    }
    return flags;
  }

  // Write text into a node as text, but with a position's badge and a pick's chip around the
  // tokens that are one. A bare place ("3.04") is only a pick when a pick word sits beside it,
  // or when the caller says the column it is in holds picks -- so a table of weekly scores keeps
  // its figures as figures.
  function decorate(parent, text, options) {
    const bare = Boolean(options && options.bare);
    let rest = String(text || '');
    let guard = 0;
    while (rest && guard < 400) {
      guard += 1;
      PICK_TOKEN.lastIndex = 0;
      POSITION_TOKEN.lastIndex = 0;
      const pick = PICK_TOKEN.exec(rest);
      const position = POSITION_TOKEN.exec(rest);
      let hit = null;
      let kind = '';
      if (pick && position) {
        kind = pick.index <= position.index ? 'pick' : 'position';
        hit = kind === 'pick' ? pick : position;
      } else if (pick) {
        hit = pick;
        kind = 'pick';
      } else if (position) {
        hit = position;
        kind = 'position';
      }
      if (!hit) {
        break;
      }
      if (kind === 'pick' && hit[1] && !bare) {
        const beside = rest.slice(Math.max(0, hit.index - 24), hit.index) +
                       rest.slice(hit.index + hit[0].length, hit.index + hit[0].length + 24);
        if (!PICK_WORDS.test(beside)) {
          parent.appendChild(document.createTextNode(rest.slice(0, hit.index + hit[0].length)));
          rest = rest.slice(hit.index + hit[0].length);
          continue;
        }
      }
      if (hit.index > 0) {
        parent.appendChild(document.createTextNode(rest.slice(0, hit.index)));
      }
      const chip = document.createElement('span');
      chip.textContent = hit[0];
      chip.className = kind === 'pick' ? 'ask-pick' : 'position pos-' + hit[0].toLowerCase();
      parent.appendChild(chip);
      rest = rest.slice(hit.index + hit[0].length);
    }
    if (rest) {
      parent.appendChild(document.createTextNode(rest));
    }
  }

  function buildTable(lines) {
    const grid = lines.map(splitCells);
    // A row the model happened to begin with a list marker is still a row, so the marker is
    // dropped rather than left sitting in the first cell. Only a marker followed by a space
    // counts -- "3.01" is a place, not a numbered line.
    grid.forEach(function (cells) {
      if (cells.length) {
        cells[0] = cells[0].replace(/^[-*\u2022]\s+/, '');
      }
    });
    let head = null;
    if (grid.length > 1 && grid[1].length > 0 && grid[1].every(function (cell) {
      return DASHES.test(cell);
    })) {
      head = grid.shift();
      grid.shift();
    }
    const width = head ? head.length : Math.max.apply(null, grid.map(function (cells) { return cells.length; }));
    grid.forEach(function (cells) {
      while (cells.length < width) {
        cells.push('');
      }
    });
    const numeric = numbersIn(head, grid);
    // A column that names picks holds one per cell, so a bare place in it is a pick; a column of
    // figures is figures, and nothing in one is dressed up.
    const picks = [];
    for (let column = 0; column < width; column += 1) {
      picks.push(!numeric[column] && PICK_COLUMN.test(head ? (head[column] || '') : ''));
    }
    const table = document.createElement('table');
    table.className = 'ask-table';
    const cell = function (tag, text, column, header) {
      const node = document.createElement(tag);
      if (header || numeric[column]) {
        node.textContent = text;
      } else {
        decorate(node, text, { bare: picks[column] });
      }
      // A figure is lined up so a column of them reads down; the mark for a stat this player's
      // position has none of is the same column, set as the nothing it is.
      const nothing = !header && BLANK.test(String(text || '').trim());
      const classes = [];
      if (!header && numeric[column]) {
        classes.push('ask-cell-number');
      }
      if (nothing) {
        classes.push('ask-cell-blank');
      }
      if (classes.length) {
        node.className = classes.join(' ');
      }
      return node;
    };
    if (head) {
      const thead = document.createElement('thead');
      const tr = document.createElement('tr');
      head.forEach(function (text, column) {
        const th = cell('th', text, column, true);
        if (numeric[column]) {
          th.className = 'ask-cell-number';
        }
        tr.appendChild(th);
      });
      thead.appendChild(tr);
      table.appendChild(thead);
    }
    const tbody = document.createElement('tbody');
    grid.forEach(function (cells) {
      const tr = document.createElement('tr');
      cells.forEach(function (text, column) {
        tr.appendChild(cell('td', text, column, false));
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    const wrap = document.createElement('div');
    wrap.className = 'ask-table-wrap';
    wrap.appendChild(table);
    return wrap;
  }

  // A bullet or a numbered line the model wrote becomes a real list item, a run of cell lines a
  // table, and anything else a paragraph. Only the marker is read -- an answer full of dashes
  // stays a paragraph.
  function fillAnswer(text) {
    body.textContent = '';
    const lines = String(text || '').split(/\r?\n/);
    let list = null;
    let index = 0;
    while (index < lines.length) {
      const line = lines[index].trim();
      if (!line) {
        list = null;
        index += 1;
        continue;
      }
      if (isCellLine(line)) {
        let end = index;
        while (end < lines.length && lines[end].trim() && isCellLine(lines[end])) {
          end += 1;
        }
        if (end - index > 1) {
          body.appendChild(buildTable(lines.slice(index, end).map(function (raw) {
            return raw.trim();
          })));
          list = null;
          index = end;
          continue;
        }
      }
      const bullet = /^[-*•]\s+(.*)$/.exec(line);
      const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
      if (bullet || numbered) {
        const wanted = bullet ? 'ul' : 'ol';
        if (!list || list.tagName.toLowerCase() !== wanted) {
          list = document.createElement(wanted);
          body.appendChild(list);
        }
        const item = document.createElement('li');
        decorate(item, (bullet || numbered)[1]);
        list.appendChild(item);
        index += 1;
        continue;
      }
      list = null;
      const paragraph = document.createElement('p');
      decorate(paragraph, line.replace(/^#+\s*/, '').replace(/\*\*(.+?)\*\*/g, '$1'));
      body.appendChild(paragraph);
      index += 1;
    }
    if (!body.childNodes.length) {
      body.textContent = 'No answer came back.';
    }
  }

  function fillSteps(used) {
    steps.textContent = '';
    if (!used || !used.length) {
      trace.hidden = true;
      return;
    }
    used.forEach(function (step) {
      const item = document.createElement('li');
      if (step.failed) {
        item.className = 'ask-step-failed';
      }
      const name = document.createElement('b');
      name.textContent = String(step.tool || '').replace(/_/g, ' ');
      item.appendChild(name);
      const args = Object.keys(step.arguments || {});
      if (args.length) {
        const detail = document.createElement('code');
        detail.textContent = args.map(function (key) {
          return key + '=' + JSON.stringify(step.arguments[key]);
        }).join('  ');
        item.appendChild(detail);
      }
      if (step.summary) {
        const summary = document.createElement('span');
        summary.textContent = step.summary;
        item.appendChild(summary);
      }
      steps.appendChild(item);
    });
    trace.hidden = false;
  }

  function show(result) {
    panel.hidden = false;
    panel.classList.remove('is-pending');
    if (!result || result.ok !== true) {
      panel.classList.add('is-error');
      body.textContent = (result && result.error) || 'That question could not be answered.';
      meta.textContent = '';
      fillSteps(result && result.steps);
      return;
    }
    panel.classList.remove('is-error');
    fillAnswer(result.answer);
    const lookups = (result.steps || []).length;
    meta.textContent = [
      result.seconds ? result.seconds + 's' : '',
      lookups ? lookups + (lookups === 1 ? ' lookup' : ' lookups') : 'no lookups',
    ].filter(Boolean).join(' · ');
    fillSteps(result.steps);
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) {
      input.focus();
      return;
    }
    button.disabled = true;
    button.textContent = 'Asking';
    panel.hidden = false;
    panel.classList.add('is-pending');
    panel.classList.remove('is-error');
    body.textContent = 'Reading this league…';
    meta.textContent = '';
    trace.hidden = true;

    fetch(form.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ question: question }),
    }).then(function (response) {
      return response.json();
    }).then(function (result) {
      show(result);
    }).catch(function () {
      show({ ok: false, error: 'The app could not be reached to answer that. Try again.' });
    }).finally(function () {
      button.disabled = false;
      button.textContent = 'Ask';
      panel.classList.remove('is-pending');
    });
  });
})();
