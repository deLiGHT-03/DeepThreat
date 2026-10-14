/* Copy the comparison's own address, so the same table can be pasted into a league chat.

   The address is read off the page rather than built here: whatever the comparison was opened
   with -- the host it is served from and any query it arrived by -- is exactly what reopens it,
   and composing one instead would be guessing at a host that is already in the bar. */

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
