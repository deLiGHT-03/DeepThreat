/* The compare panel on the Pace board: folding the card away, and copying the address that
   brings it back.

   The card is opened by the server, not here: an address carrying two names is what draws the
   comparison, and it is drawn folded out when the page arrives. This only folds it back -- the
   reader who has read the two players and wants the board can put the card away without losing
   the search, and open it again -- and the address is left alone while he does, because the
   comparison is still the one being read: it is hidden under the board, not forgotten.

   The copy button hands back the page's own address, which is now the whole of the view -- the
   board, the position it is narrowed to, the column it is read by and the two players in the
   panel -- so pasting it into a league chat opens the board with the same comparison above it. */

(() => {
  const panel = document.getElementById("compare-panel");
  const fold = panel && panel.querySelector("[data-compare-fold]");
  if (panel && fold) {
    // The state lives in one place -- the panel's own class -- and the button's words and its
    // aria-expanded are read off it, so a fold the reader makes and one the page arrives with
    // cannot disagree about whether the card is out.
    const setOpen = (open) => {
      panel.classList.toggle("is-open", open);
      fold.setAttribute("aria-expanded", open ? "true" : "false");
      fold.textContent = open ? "Hide" : "Show";
    };
    setOpen(panel.classList.contains("is-open"));
    fold.addEventListener("click", () => setOpen(!panel.classList.contains("is-open")));
  }

  document.querySelectorAll("[data-copy-link]").forEach((button) => {
    const label = button.querySelector("span") || button;
    button.addEventListener("click", async () => {
      const original = label.textContent;
      // The clipboard needs a secure origin and a permission that can be refused; where either
      // is missing the reader still has the address in the bar, so the button says so rather
      // than claiming a copy that did not happen.
      try {
        await navigator.clipboard.writeText(window.location.href);
        label.textContent = "Link copied";
      } catch (_) {
        label.textContent = "Copy from the address bar";
      }
      button.disabled = true;
      window.setTimeout(() => {
        label.textContent = original;
        button.disabled = false;
      }, 2000);
    });
  });
})();
