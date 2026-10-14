/* The compare panel on the Pace board: copying the address that brings the comparison back.

   The panel is drawn by the server and is all there is of it -- two boxes, and the card the two
   names produce -- because a comparison is asked for here rather than opened and closed: a reader
   either wants one or wants the board, and a board with a panel beside it is still a board.

   The copy button hands back the page's own address, which is now the whole of the view -- the
   board, the position it is narrowed to, the column it is read by and the two players in the
   panel -- so pasting it into a league chat opens the board with the same comparison beside it. */

(() => {
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
