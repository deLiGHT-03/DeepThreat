/* Save an element as a PNG.

   A picture of the page is rendered in a document of its own, so it shares neither the page's
   stylesheets nor the fonts those stylesheets loaded. That is why every style is copied inline
   here, and why the fonts have to be carried across as data URLs: without them the picture is
   re-measured in a generic face, and text that fits on the page arrives clipped or misaligned.
   The picture is handed to the browser as a data: URL rather than a blob: one, because Chromium
   taints the canvas for the latter as soon as the picture contains a foreignObject, which fails
   the export outright.
*/

//: Sleeper's CDN answers a page without Access-Control-Allow-Origin, so its bytes cannot be
//: read back where they stand.
const CDN_ORIGIN = "https://sleepercdn.com";
const MEDIA_PREFIX = "/media";

/** A URL whose bytes this page is allowed to read, relaying the CDN through our own origin. */
function readableSource(source) {
  try {
    const url = new URL(source, document.baseURI);
    return url.origin === CDN_ORIGIN ? `${MEDIA_PREFIX}${url.pathname}` : url.href;
  } catch (error) {
    return source;
  }
}

const toDataUrl = (blob) => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result);
  reader.onerror = reject;
  reader.readAsDataURL(blob);
});

//: A family that asks for no particular face, so nothing needs carrying across for it.
const GENERIC_FAMILIES = new Set([
  "sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
  "ui-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong", "inherit", "initial",
  "unset", "revert",
]);

//: Font bytes and @font-face rules are the same for every snapshot in a session, so read each once.
const fontData = new Map();
let faceRules = null;

/** The first family in a computed font stack, or "" when it names only a generic family. */
function leadingFamily(stack) {
  const first = stack.split(",")[0].replace(/["']/g, "").trim().toLowerCase();
  return GENERIC_FAMILIES.has(first) ? "" : first;
}

/** A font URL as an absolute one, or "" when it cannot be resolved. */
function resolveUrl(url, base) {
  if (!url) return "";
  try {
    return new URL(url, base || undefined).href;
  } catch (error) {
    return "";
  }
}

/** The @font-face rule behind a stylesheet rule, with its URL resolved against that sheet. */
function ruleFace(rule, base) {
  const value = (name) => rule.style.getPropertyValue(name);
  const source = value("src").match(/url\((['"]?)([^'")]+)\1\)/);
  return {
    family: value("font-family"),
    style: value("font-style"),
    weight: value("font-weight"),
    display: value("font-display"),
    range: value("unicode-range"),
    url: resolveUrl(source && source[2], base),
  };
}

/** The @font-face rules inside a stylesheet the browser would not let us read as rules. */
function cssFaces(css, base) {
  return [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map((match) => {
    const body = match[1];
    // Property names are matched after a separator so font-weight cannot match font-style.
    const value = (name) => (body.match(new RegExp(`(?:^|;)\\s*${name}\\s*:\\s*([^;]+)`, "i")) || [])[1] || "";
    const source = value("src").match(/url\((['"]?)([^'")]+)\1\)/);
    return {
      family: value("font-family"),
      style: value("font-style"),
      weight: value("font-weight"),
      display: value("font-display"),
      range: value("unicode-range"),
      url: resolveUrl(source && source[2], base),
    };
  }).filter((face) => face.url && face.family);
}

/**
 * Every @font-face the page can render with.
 *
 * Our own stylesheets give up their rules directly, but the Google Fonts sheet arrives through
 * an @import, so it is either read as a nested sheet or fetched as text — the font CDN serves
 * both its stylesheet and its files with CORS enabled, which is what makes that possible.
 */
async function collectFaceRules() {
  const faces = [];
  const sheetsToFetch = [];
  for (const sheet of document.styleSheets) {
    let rules = null;
    try {
      rules = [...sheet.cssRules];  // throws for a stylesheet from another origin
    } catch (error) {
      if (sheet.href) sheetsToFetch.push(sheet.href);
      continue;
    }
    for (const rule of rules) {
      if (rule.styleSheet) {
        const imported = rule.styleSheet;
        let nested = null;
        try {
          nested = [...imported.cssRules];
        } catch (error) {
          nested = null;
        }
        if (nested) faces.push(...nested.filter((child) => child.style?.src).map((child) => ruleFace(child, imported.href)));
        else if (imported.href) sheetsToFetch.push(imported.href);
      } else if (rule.style?.src) {
        faces.push(ruleFace(rule, sheet.href));
      }
    }
  }
  for (const href of sheetsToFetch) {
    try {
      const response = await fetch(href, { mode: "cors", credentials: "omit" });
      if (response.ok) faces.push(...cssFaces(await response.text(), href));
    } catch (error) {
      // A stylesheet that will not load simply contributes no faces to carry across.
    }
  }
  return faces.filter((face) => face.family && face.url);
}

/** The newest spelling of every code point that appears in the snapshot's text. */
function usedCodePoints(root) {
  const points = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    for (const character of walker.currentNode.data) points.add(character.codePointAt(0));
  }
  return points;
}

/**
 * Whether a face's unicode-range holds any of the characters in this snapshot.
 *
 * Google Fonts splits every family into per-script faces, so this is what keeps a snapshot down
 * to the faces its own text needs instead of every script the family covers.
 */
function rangeCoversAny(unicodeRange, points) {
  if (!unicodeRange) return true;  // no range at all covers whatever it is asked for
  for (const part of unicodeRange.split(",")) {
    const [from, to] = part.trim().replace(/^U\+/i, "").split("-");
    if (!from) continue;
    const start = parseInt(from.replace(/\?/g, "0"), 16);
    const end = parseInt((to || from).replace(/\?/g, "F"), 16);
    for (const point of points) if (point >= start && point <= end) return true;
  }
  return false;
}

/** One @font-face rule carrying the face itself as a data: URL. */
function faceCss(face, data) {
  const descriptors = [`font-family:${face.family}`, `src:url("${data}")`];
  if (face.style) descriptors.push(`font-style:${face.style}`);
  if (face.weight) descriptors.push(`font-weight:${face.weight}`);
  if (face.display) descriptors.push(`font-display:${face.display}`);
  if (face.range) descriptors.push(`unicode-range:${face.range}`);
  return `@font-face{${descriptors.join(";")}}`;
}

/**
 * The @font-face rules a snapshot needs, with the font files they name inlined.
 *
 * Only the families the clone actually uses and the faces that can cover its text are carried
 * over, so a snapshot stays close to the size of its markup. A face that cannot be fetched is
 * left out and falls back, exactly as the picture did before any of this.
 */
async function embedFonts(clone, families) {
  if (!families.size) return "";
  faceRules = faceRules || collectFaceRules();
  const points = usedCodePoints(clone);
  const css = [];
  for (const face of await faceRules) {
    const family = face.family.replace(/["']/g, "").trim().toLowerCase();
    if (!families.has(family) || !rangeCoversAny(face.range, points)) continue;
    let data = fontData.get(face.url);
    if (!data) {
      try {
        const response = await fetch(face.url, { mode: "cors", credentials: "omit" });
        if (!response.ok) continue;
        data = await toDataUrl(await response.blob());
        fontData.set(face.url, data);
      } catch (error) {
        continue;
      }
    }
    css.push(faceCss(face, data));
  }
  return css.join("");
}

async function prepareImages(clone) {
  await Promise.all([...clone.querySelectorAll("img")].map(async (image) => {
    const source = image.currentSrc || image.src;
    if (!source) return;
    try {
      const response = await fetch(readableSource(source),
                                   { mode: "cors", credentials: "omit" });
      if (!response.ok) throw new Error("Image unavailable");
      image.src = await toDataUrl(await response.blob());
    } catch {
      image.removeAttribute("src");
      image.style.visibility = "hidden";
    }
  }));
}

/**
 * How much room the picture needs: the element's border box, grown to hold any content that
 * spills out of it.
 *
 * The border box rather than the content one, because the clone is laid out with it: measuring
 * the content box would drop the element's border and, worse, squeeze everything inside by that
 * border's width, so text that just fits on the page would arrive clipped in the picture.
 */
function snapshotSize(element) {
  const box = element.getBoundingClientRect();
  return {
    width: Math.ceil(Math.max(box.width, element.scrollWidth)),
    height: Math.ceil(Math.max(box.height, element.scrollHeight)),
  };
}

async function saveElementAsPng(element, filename) {
  const { width, height } = snapshotSize(element);
  const clone = element.cloneNode(true);
  const originalNodes = [element, ...element.querySelectorAll("*")];
  const cloneNodes = [clone, ...clone.querySelectorAll("*")];
  const families = new Set();

  originalNodes.forEach((node, index) => {
    const copy = cloneNodes[index];
    if (!copy || !node.style) return;
    const computed = getComputedStyle(node);
    for (let styleIndex = 0; styleIndex < computed.length; styleIndex += 1) {
      const property = computed.item(styleIndex);
      copy.style.setProperty(property, computed.getPropertyValue(property));
    }
    families.add(leadingFamily(computed.fontFamily));
    if (computed.position === "sticky" || computed.position === "fixed") {
      copy.style.position = "static";
      copy.style.left = "auto";
      copy.style.top = "auto";
    }
  });
  clone.querySelectorAll("[data-screenshot-ignore]").forEach((node) => node.remove());
  clone.style.width = `${width}px`;
  clone.style.height = `${height}px`;
  clone.style.maxWidth = "none";
  clone.style.maxHeight = "none";
  clone.style.overflow = "visible";
  clone.style.margin = "0";
  families.delete("");
  const [fonts] = await Promise.all([embedFonts(clone, families), prepareImages(clone)]);

  const foreignObject = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
  foreignObject.setAttribute("width", String(width));
  foreignObject.setAttribute("height", String(height));
  const xhtml = document.createElement("div");
  xhtml.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  xhtml.style.width = `${width}px`;
  xhtml.style.height = `${height}px`;
  xhtml.append(clone);
  foreignObject.append(xhtml);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  if (fonts) {
    // The picture is a document of its own, so its fonts have to travel inside it.
    const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
    style.textContent = fonts;
    svg.append(style);
  }
  svg.append(foreignObject);
  const svgUrl = "data:image/svg+xml;charset=utf-8,"
    + encodeURIComponent(new XMLSerializer().serializeToString(svg));

  const image = new Image();
  image.src = svgUrl;
  await image.decode();
  const scale = 2;
  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext("2d");
  context.scale(scale, scale);
  context.drawImage(image, 0, 0, width, height);
  const png = await new Promise((resolve, reject) => canvas.toBlob(
    (blob) => blob ? resolve(blob) : reject(new Error("Could not create image")), "image/png"));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(png);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest?.("[data-screenshot-target]");
  if (!button) return;
  const target = document.getElementById(button.dataset.screenshotTarget);
  if (!target) return;

  const originalMarkup = button.innerHTML;
  button.disabled = true;
  button.innerHTML = "…";
  const safeName = (target.dataset.screenshotName || button.dataset.screenshotTarget)
    .replace(/[^a-z0-9-_]+/gi, "-");
  try {
    await saveElementAsPng(target, `${safeName}.png`);
  } catch (error) {
    console.error("Screenshot failed", error);
    window.alert("The screenshot could not be created in this browser. Please try again.");
  } finally {
    button.disabled = false;
    button.innerHTML = originalMarkup;
  }
});
