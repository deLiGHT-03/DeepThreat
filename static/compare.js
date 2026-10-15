/* The compare panel on the Pace board: the fold it opens with where the page is narrow, and the
   button that copies the address which brings the comparison back.

   The panel is drawn by the server and is all there is of it -- two boxes, and the card the two
   names produce -- because a comparison is asked for here rather than opened and closed: a reader
   either wants one or wants the board, and a board with a panel beside it is still a board.

   The one thing that is folded is the panel itself, and only where the page has no column to read
   it in beside the board: there it stands above the figures the page is for, so it opens as a line
   of its own and the boxes are one tap away. The fold is the script's business rather than the
   stylesheet's -- what is folded is a plain form, and a browser that never ran this would be left
   with two boxes it could not unfold -- and it is the reader's answer that is remembered, not the
   window's: resizing does not undo a fold he made, and the panel that answers his own comparison
   is not folded at all.

   The copy button hands back the page's own address, which is now the whole of the view -- the
   board, the position it is narrowed to, the column it is read by and the two players in the
   panel -- so pasting it into a league chat opens the board with the same comparison beside it. */

(() => {
  const panel = document.querySelector("[data-compare-panel]");
  const toggle = panel ? panel.querySelector("[data-compare-toggle]") : null;
  if (panel && toggle) {
    // The width at which the panel stops being a column beside the board: above it there is
    // nothing to fold, and the stylesheet does not draw the control either.
    const besideTheBoard = window.matchMedia("(min-width: 1080px)");
    // A comparison of the reader's own, which is one he typed into the two boxes: the board he
    // lands on is the answer to a question he asked, and folding it would make him ask twice.
    const asked = new URLSearchParams(window.location.search).has("player");
    // His own answer to the fold, or null while nobody has given one.
    let choice = null;

    const render = () => {
      const folded = !besideTheBoard.matches && (choice === null ? !asked : choice);
      panel.classList.toggle("is-folded", folded);
      toggle.setAttribute("aria-expanded", folded ? "false" : "true");
      const label = folded ? "Show the comparison" : "Hide the comparison";
      toggle.setAttribute("aria-label", label);
      toggle.title = label;
    };

    toggle.addEventListener("click", () => {
      choice = !panel.classList.contains("is-folded");
      render();
    });
    // A window resized across the breakpoint is a different page's worth of room, but not a
    // different reader: his own fold is kept, and where he has not made one the fold is asked
    // again for the width the page now has. The window is listened to rather than the query,
    // which is the way the rest of this app watches a window change, and the query is read again
    // when it happens -- one place that answers "is the panel beside the board" for both.
    window.addEventListener("resize", render);
    render();
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
