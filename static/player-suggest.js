/* A player's name, finished for the reader in a compare box.

   A reader types the first letters of a name and the rest of it appears faintly after the caret;
   Tab, Enter or a click on the faint part takes it, so the name arrives without being typed out
   in full. Any input carrying data-player-suggest opts in, which is what lets the box a player
   card builds for itself take part without this file knowing anything about cards.

   The names come from the league's own players -- exactly the set the lookup behind the box
   answers for -- asked for once, the first time one of these boxes is used, and ordered by what
   each player is on pace for, so the suggestion is the name this league would answer about
   first. A reader who never types in one of these boxes fetches none of it.

   The completion finishes what is being typed: the rest of the name once the name itself has
   been started ("jah" gives "myr Gibbs"), and the rest of a surname once one of its words has
   ("gib" gives "bs"), which is a name the lookup finds on its own -- a surname is enough there.
   A name this league does not hold, an exact one, and one already completed all leave the box
   with nothing faint in it. */

(() => {
  "use strict";

  const leagueId = (window.location.pathname.match(/^\/league\/([^/]+)\//) || [])[1];
  if (!leagueId) return;

  const TRIGGER = "[data-player-suggest]";

  // One character of a name, as it is compared here: lowercase, accents dropped, and the
  // punctuation a reader may or may not type -- an apostrophe, a full stop, a hyphen -- taken
  // out, so "Ja'Marr" and "jamarr" are the same word.
  function folded(character) {
    const base = character.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    let kept = "";
    for (const part of base) {
      if (part === "'" || part === "\u2019" || part === "." || part === "-") continue;
      kept += part;
    }
    return kept;
  }

  function fold(text) {
    let out = "";
    for (const character of String(text || "")) out += folded(character);
    return out;
  }

  // A name, its folded form, where each folded character came from, and its words. Keeping the
  // places is what lets the completion be taken from the name itself rather than from the folded
  // copy of it, which is shorter than the name whenever a name has an apostrophe or an accent.
  function analyse(name) {
    const raw = String(name || "");
    let text = "";
    const places = [];
    for (let index = 0; index < raw.length; index += 1) {
      for (const character of folded(raw[index])) {
        text += character;
        places.push(index);
      }
    }
    const words = [];
    const pattern = /\S+/g;
    let match = pattern.exec(text);
    while (match !== null) {
      words.push({text: match[0], start: match.index, end: match.index + match[0].length});
      match = pattern.exec(text);
    }
    return {raw, text, places, words};
  }

  // The suggestion for what has been typed: the faint ``rest`` to draw after the caret, and the
  // ``name`` to leave in the box if it is taken. A name whose own beginning has been typed is
  // completed whole, and taking it leaves the league's own spelling of the name -- "ja'marr"
  // becomes "Ja'Marr Chase" -- which is the spelling the table underneath will show. Failing
  // that, the word being typed is finished, and only that word: a surname is a name the lookup
  // finds on its own, and replacing it with one particular player's full name would take it off
  // the reader.
  function suggestionFor(value, players) {
    const query = fold(value);
    if (!query) return null;
    for (const who of players) {
      if (who.text.startsWith(query) && who.text.length > query.length) {
        return {rest: who.raw.slice(who.places[query.length]), name: who.raw};
      }
    }
    if (query.includes(" ")) return null;
    for (const who of players) {
      for (const word of who.words) {
        if (word.text.startsWith(query) && word.text.length > query.length) {
          return {rest: who.raw.slice(who.places[word.start + query.length],
                                      who.places[word.end - 1] + 1), name: null};
        }
      }
    }
    return null;
  }

  let asked = null;
  function players() {
    if (!asked) {
      asked = fetch(`/league/${encodeURIComponent(leagueId)}/players`,
                    {headers: {Accept: "application/json"}})
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => ((data && data.names) || []).map(analyse))
        .catch(() => []);
    }
    return asked;
  }

  const ghosts = new WeakMap(); // input -> the faint copy drawn over it
  const boxes = new Set(); // every box that has one, so the others can be swept clear

  function ghostFor(input) {
    const known = ghosts.get(input);
    if (known) return known;
    const host = input.parentElement;
    if (!host) return null;
    if (getComputedStyle(host).position === "static") host.classList.add("player-suggest-host");
    const ghost = document.createElement("div");
    ghost.className = "player-suggest-ghost";
    ghost.hidden = true;
    ghost.setAttribute("aria-hidden", "true");
    const typed = document.createElement("span");
    typed.className = "player-suggest-typed";
    const rest = document.createElement("span");
    rest.className = "player-suggest-rest";
    ghost.append(typed, rest);
    host.append(ghost);
    const made = {ghost, typed, rest, suggestion: null};
    // The faint part is the suggestion, so a press on it takes it. The press is swallowed so the
    // box keeps the focus the name is about to be typed into.
    rest.addEventListener("mousedown", (event) => {
      event.preventDefault();
      take(input, made);
      input.focus();
    });
    ghosts.set(input, made);
    boxes.add(input);
    return made;
  }

  // The ghost is laid over the box in the box's own font, padding and border, so the faint text
  // continues the typed text exactly where the box's own text would have continued it.
  function align(input, made) {
    const styles = getComputedStyle(input);
    const css = made.ghost.style;
    css.left = `${input.offsetLeft}px`;
    css.top = `${input.offsetTop}px`;
    css.width = `${input.offsetWidth}px`;
    css.height = `${input.offsetHeight}px`;
    css.padding = styles.padding;
    css.font = styles.font;
    css.letterSpacing = styles.letterSpacing;
    css.borderRadius = styles.borderRadius;
    css.borderWidth = styles.borderWidth;
    css.borderStyle = "solid";
    css.borderColor = "transparent";
    // The faint half is the box's own text colour at less of it, which is a suggestion on a light
    // theme and on a dark one without a colour of its own to keep in step with either.
    css.color = styles.color;
  }

  function present(input, made, suggestion) {
    made.suggestion = suggestion;
    if (!suggestion || document.activeElement !== input) {
      made.ghost.hidden = true;
      return;
    }
    align(input, made);
    made.typed.textContent = input.value;
    made.rest.textContent = suggestion.rest;
    made.ghost.hidden = false;
    // A name longer than its box scrolls in the input; the copy over it scrolls with it.
    made.ghost.scrollLeft = input.scrollLeft;
  }

  function refresh(input) {
    const made = ghostFor(input);
    if (!made) return;
    if (!input.value.trim()) {
      present(input, made, null);
      return;
    }
    const wanted = input.value;
    players().then((list) => {
      if (input.value !== wanted || document.activeElement !== input) return;
      present(input, made, suggestionFor(wanted, list));
    });
  }

  function take(input, made) {
    if (!made.suggestion) return;
    input.value = made.suggestion.name || input.value + made.suggestion.rest;
    present(input, made, null);
    try {
      input.setSelectionRange(input.value.length, input.value.length);
    } catch (error) {
      // A box that will not take a caret is left with the name in it, which is the point.
    }
    // Told about the change, so anything watching the box -- and this file's own listener, which
    // now finds nothing left to complete -- sees the finished name.
    input.dispatchEvent(new Event("input", {bubbles: true}));
  }

  const suggests = (node) => node instanceof Element && node.matches(TRIGGER);

  // Nothing faint is left showing over a box that is not the one being typed in. Focus moving
  // between two of these boxes puts one away, and the window losing focus puts them all away,
  // without waiting to be told: only the box the caret is in has a suggestion to offer.
  function clearOthers(except) {
    for (const box of boxes) {
      const made = box === except ? null : ghosts.get(box);
      if (made) present(box, made, null);
    }
  }

  document.addEventListener("focusin", (event) => {
    if (!suggests(event.target)) return;
    clearOthers(event.target);
    refresh(event.target);
  });

  document.addEventListener("input", (event) => {
    if (suggests(event.target)) refresh(event.target);
  });

  document.addEventListener("focusout", (event) => {
    if (!suggests(event.target)) return;
    const made = ghosts.get(event.target);
    if (made) present(event.target, made, null);
  });

  window.addEventListener("blur", () => clearOthers(null));

  document.addEventListener("keydown", (event) => {
    const input = event.target;
    if (!suggests(input)) return;
    const made = ghosts.get(input);
    if (!made || !made.suggestion || made.ghost.hidden) return;
    const atEnd = input.selectionStart === input.value.length
      && input.selectionStart === input.selectionEnd;
    if (event.key === "Tab" || event.key === "Enter" || (event.key === "ArrowRight" && atEnd)) {
      event.preventDefault();
      take(input, made);
    } else if (event.key === "Escape") {
      // Dismissed until the next keystroke, which works the suggestion out again.
      present(input, made, null);
    }
  });
})();
