/* The Expand switch in the league rail's header: one checkbox opens every trade box in the
   rail, so a manager can read the whole season at once instead of clicking each one. The
   selector it opens comes from the markup rather than from here. */
document.addEventListener("change", (event) => {
  const toggle = event.target.closest("[data-expand-target]");
  if (!toggle) return;

  const scope = document.querySelector(toggle.getAttribute("data-expand-target"));
  if (!scope) return;

  for (const folded of scope.querySelectorAll("details")) {
    folded.open = toggle.checked;
  }
});
