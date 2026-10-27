// Draws one caption chunk in the browser the way the burn will look (an approximation of the ASS
// effects): used by the live overlay on the player, the style gallery and the Short preview.

const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Paint `words` ([{text, emph}]) into `target` for `style`.
 * @param {HTMLElement} target
 * @param {object} style  preset JSON
 * @param {{text: string, emph?: boolean}[]} words
 * @param {number} active  index of the word being spoken (-1 before the first)
 * @param {number} frac    0..1 through the active word (karaoke fill, typewriter letters)
 * @param {number} fontPx  font size in CSS pixels
 * @param {number} brk     index of the first word on line 2 (0 = one line)
 */
export function paintCaption(target, style, words, active, frac, fontPx, brk = 0) {
  const effect = style.effect || "highlight";
  const typed = effect === "typewriter" ? Math.ceil(frac * (words[active]?.text.length || 0)) : 0;
  const flags = words.map((w) => (w.emph ? 1 : 0)).join("");
  const key = `${words.map((w) => w.text).join(" ")}|${active}|${typed}|${fontPx.toFixed(1)}|${style.font}|${effect}`
    + `|${style.name || ""}|${style.accent || ""}|${flags}|${style.highlight || ""}`;
  if (target.dataset.key === key) {
    if (effect === "karaoke") target.style.setProperty("--fill", `${Math.round(frac * 100)}%`);
    return;
  }
  const sameChunk = target.dataset.key?.split("|")[0] === key.split("|")[0];
  const prevActive = sameChunk ? +target.dataset.key.split("|")[1] : -2;
  target.dataset.key = key;
  target.style.setProperty("--fill", `${Math.round(frac * 100)}%`);

  const outline = style.box ? 0 : (style.outline ?? 0.06) * fontPx;
  const shadow = (style.shadow || 0) * fontPx;
  const glow = (style.glow || 0) * fontPx;
  const shadows = [];
  if (shadow) shadows.push(`${shadow}px ${shadow}px 0 ${style.shadow_color || "#0008"}`);
  if (glow) shadows.push(`0 0 ${glow}px ${style.glow_color}`, `0 0 ${glow * 2.2}px ${style.glow_color}`);
  const color = style.color || "#fff";

  const parts = [];
  words.forEach((w, i) => {
    if (i && i === brk) parts.push(document.createElement("br"));
    else if (i) parts.push(" ");
    const span = document.createElement("span");
    span.className = "w";
    let text = w.text;
    let fill = w.emph && style.accent ? style.accent : color;
    if (effect === "typewriter") {
      if (i > active) span.style.visibility = "hidden";
      if (i === active) {
        span.append(text.slice(0, typed));
        const rest = document.createElement("span");
        rest.style.visibility = "hidden";
        rest.textContent = text.slice(typed);
        span.append(rest);
        text = null;
      }
    } else if (style.reveal && i > active) {
      span.style.visibility = "hidden";
    } else if (i === active && style.highlight) {
      if (effect === "karaoke") {
        span.style.backgroundImage = `linear-gradient(90deg, ${style.highlight} var(--fill), ${fill} var(--fill))`;
        span.style.webkitBackgroundClip = "text";
        span.style.backgroundClip = "text";
        fill = "transparent";
      } else if (effect === "boxed") {
        span.style.background = style.highlight;
        span.style.borderRadius = "0.12em";
        span.style.padding = "0 0.12em";
        fill = style.active_color || color;
      } else {
        fill = style.highlight;
        span.style.transform = `scale(${style.highlight_scale || 1})`;
      }
      if (prevActive !== active && !reduceMotion()) {
        if (effect === "bounce") span.classList.add("bounce");
        else if (style.pop_in || effect === "boxed") span.classList.add("pop");
      }
    } else if (effect === "karaoke" && i < active) {
      fill = style.highlight;
    }
    if (text !== null) span.append(text);
    span.style.color = fill;
    if (outline && !(effect === "boxed" && i === active)) {
      span.style.webkitTextStroke = `${outline * 2}px ${style.outline_color || "#000"}`;
      span.style.paintOrder = "stroke fill";
    }
    span.style.textShadow = shadows.length ? shadows.join(", ") : "none";
    parts.push(span);
  });

  const cap = document.createElement("span");
  cap.className = "cap";
  if (style.box) {
    const box = document.createElement("span");
    box.className = "box";
    box.style.background = style.box_color;
    const pad = (style.box_padding || 0.3) * fontPx;
    box.style.padding = `${pad * 0.5}px ${pad}px`;
    box.append(...parts);
    cap.append(box);
  } else {
    cap.append(...parts);
  }
  target.style.fontFamily = `"${style.font}", sans-serif`;
  target.style.fontSize = `${fontPx}px`;
  target.style.transform = style.tilt ? `rotate(${-style.tilt}deg)` : "";
  target.replaceChildren(cap);
}

/** A display form of a word for a style (uppercase, edge punctuation stripped). */
export function displayWord(text, style) {
  let t = style.strip_punct ? text.replace(/^["'“”‘’«»(\[]+|["'“”‘’«»)\].,!?;:…—–-]+$/g, "") : text;
  return style.uppercase ? t.toUpperCase() : t;
}

export const DEMO = [
  { text: "In", emph: false }, { text: "1666", emph: true }, { text: "London", emph: true }, { text: "burned", emph: false },
];

/** Loop the demo phrase in a tile. Returns a stop function. */
export function animateDemo(target, style, sizeOf, perWord = 0.55) {
  let raf = 0;
  const words = DEMO.map((w) => ({ ...w, text: displayWord(w.text, style) }));
  const n = words.length;
  const frame = (now) => {
    const cycle = perWord * (n + 1.2);
    const t = reduceMotion() ? perWord * 1.5 : (now / 1000) % cycle;
    const active = Math.min(n - 1, Math.floor(t / perWord));
    const frac = Math.min(1, (t - active * perWord) / perWord);
    const brk = n > 2 && (style.chunk?.max_line_chars || 40) < 18 ? 2 : 0;
    if (target.offsetParent) paintCaption(target, style, words, active, frac, sizeOf(), brk); // skip while hidden
    raf = reduceMotion() ? 0 : requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(raf);
}
