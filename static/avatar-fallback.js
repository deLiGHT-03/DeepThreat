document.addEventListener("error", (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || !image.matches("[data-avatar-fallbacks]")) return;

  const urls = image.dataset.avatarFallbacks.split("|").filter(Boolean);
  const nextIndex = Number(image.dataset.avatarFallbackIndex || 0);
  if (nextIndex < urls.length) {
    image.dataset.avatarFallbackIndex = String(nextIndex + 1);
    image.src = urls[nextIndex];
  } else {
    image.hidden = true;
    if (image.nextElementSibling?.classList.contains("team-logo-placeholder")) {
      image.nextElementSibling.hidden = false;
    }
  }
}, true);
