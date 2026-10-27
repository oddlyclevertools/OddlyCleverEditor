// Captions tab: transcribe, edit the transcript in place, preview a style live, burn it.
import { api, el, fmt, mediaUrl, reloadProject, runJob, state, stripUrl, toast } from "/static/app.js";
import { animateDemo, paintCaption } from "/static/captionfx.js";
import { confetti } from "/static/fx.js";

const $ = (id) => document.getElementById(id);
let chunks = [];        // preview chunks for the chosen style (from the server's chunker)
let flatWords = [];     // [{start, end, span}] for the transcript highlight
let dirty = false;
let lastWord = -1;
let galleryFor = null;

/** Binary search: index of the last item whose start <= t, or -1. */
function findAt(items, t) {
  let lo = 0, hi = items.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid].start <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

export const hasUnsavedEdits = () => dirty;

/** Forget local edits (the user chose to leave without saving). */
export function discardEdits() {
  setDirty(false);
  state.transcript = null;
  state.transcriptFor = null;
}

function setDirty(value) {
  dirty = value;
  $("dirty").hidden = !value;
  $("save").disabled = !value;
}

// --- transcript list -----------------------------------------------------------
/** New words for an edited line: same timing if the word count matches, else spread by length. */
export function retime(words, text) {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length || !words.length) return [];
  if (tokens.length === words.length) return words.map((w, i) => ({ ...w, text: tokens[i] }));
  const start = words[0].start, end = words[words.length - 1].end;
  const weights = tokens.map((t) => t.length + 1);
  const total = weights.reduce((a, b) => a + b, 0);
  let t = start;
  return tokens.map((tok, i) => {
    const d = ((end - start) * weights[i]) / total;
    const w = { start: +t.toFixed(3), end: +(t + d).toFixed(3), text: tok, prob: 1 };
    t += d;
    return w;
  });
}

function renderLine(seg, si) {
  const text = el("div", { class: "text", dataset: { si } });
  seg.words.forEach((w, wi) => {
    const span = el("span", { class: `wd${w.prob < 0.5 ? " low" : ""}`, dataset: { si, wi },
      title: w.prob < 0.5 ? "Whisper wasn't sure about this word" : null }, w.text);
    text.append(span, " ");
  });
  if (!seg.words.length) text.append(el("span", { class: "muted" }, "(empty)"));
  return el("div", { class: "line", dataset: { si } },
    el("button", { class: "t", type: "button", title: "Play from here", onclick: () => seek(seg.start) }, fmt(seg.start)),
    text);
}

function renderLines() {
  const box = $("lines");
  box.replaceChildren(...state.transcript.segments.map(renderLine));
  indexWords();
}

function indexWords() {
  flatWords = [];
  document.querySelectorAll("#lines .wd").forEach((span) => {
    const w = state.transcript.segments[+span.dataset.si].words[+span.dataset.wi];
    flatWords.push({ start: w.start, end: w.end, span });
  });
  lastWord = -1;
}

function editLine(textDiv) {
  if (textDiv.isContentEditable) return;
  const si = +textDiv.dataset.si;
  const seg = state.transcript.segments[si];
  textDiv.textContent = seg.words.map((w) => w.text).join(" ");
  try {
    textDiv.contentEditable = "plaintext-only";
  } catch {
    textDiv.contentEditable = "true"; // older Firefox: strip formatting on paste instead
    textDiv.addEventListener("paste", (e) => {
      e.preventDefault();
      document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
    }, { once: true });
  }
  textDiv.focus();
  const finish = (commit) => {
    textDiv.removeEventListener("blur", onBlur);
    textDiv.removeEventListener("keydown", onKey);
    textDiv.removeAttribute("contenteditable");
    if (commit) {
      const words = retime(seg.words, textDiv.textContent);
      const changed = words.map((w) => w.text).join(" ") !== seg.words.map((w) => w.text).join(" ");
      if (changed) {
        const segments = state.transcript.segments.map((s, i) =>
          i === si ? { ...s, words, text: words.map((w) => w.text).join(" ") } : s);
        state.transcript = { ...state.transcript, segments };
        setDirty(true);
      }
    }
    textDiv.parentElement.replaceWith(renderLine(state.transcript.segments[si], si));
    indexWords();
  };
  const onBlur = () => finish(true);
  const onKey = (e) => {
    if (e.key === "Enter") { e.preventDefault(); textDiv.blur(); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  };
  textDiv.addEventListener("blur", onBlur);
  textDiv.addEventListener("keydown", onKey);
}

// --- live overlay -----------------------------------------------------------------
function seek(t) {
  const v = $("video");
  v.currentTime = t;
  v.play().catch(() => {});
}

function overlayGeometry(v) {
  const vw = v.clientWidth, vh = v.clientHeight;
  const nw = v.videoWidth || 16, nh = v.videoHeight || 9;
  const scale = Math.min(vw / nw, vh / nh);
  return { dw: nw * scale, dh: nh * scale, ox: (vw - nw * scale) / 2, oy: (vh - nh * scale) / 2, nw, nh };
}

function drawOverlay() {
  const v = $("video"), ov = $("overlay");
  const style = state.styles[$("style-select").value];
  if (!style || state.tab !== "captions" || !chunks.length) { ov.replaceChildren(); delete ov.dataset.key; return; }
  const t = v.currentTime;
  const i = findAt(chunks, t);
  const c = i >= 0 && t < chunks[i].end ? chunks[i] : null;
  if (!c) { ov.replaceChildren(); delete ov.dataset.key; return; }
  const g = overlayGeometry(v);
  const k = g.dh / g.nh;
  const fontPx = style.size * Math.min(g.nw, g.nh) * k;
  ov.style.left = `${g.ox}px`;
  ov.style.width = `${g.dw}px`;
  ov.style.right = "auto";
  const pos = style.position || "bottom";
  ov.style.top = ov.style.bottom = "auto";
  if (pos === "top") ov.style.top = `${g.oy + style.margin_v * g.dh}px`;
  else if (pos === "center") ov.style.top = `${g.oy + g.dh / 2 - fontPx}px`;
  else ov.style.bottom = `${g.oy + style.margin_v * g.dh}px`;
  const active = c.words.findLastIndex((w) => w.start <= t);
  const w = c.words[Math.max(active, 0)];
  const next = c.words[Math.max(active, 0) + 1];
  const span = Math.max(0.05, (next ? next.start : w.end) - w.start);
  const frac = active < 0 ? 0 : Math.min(1, (t - w.start) / span);
  // highlight effects light the first word from the chunk's start, like the burn does
  const shown = style.effect === "typewriter" || style.effect === "karaoke" || style.reveal ? active : Math.max(active, 0);
  paintCaption(ov, { ...style, accent: c.accent }, c.words, shown, frac, fontPx, c.break);
}

// --- style gallery ------------------------------------------------------------------
let demoStops = [];

/** Stop the tiles' animation loops (the gallery is rebuilt next time the tab opens). */
export function stopGallery() {
  demoStops.forEach((stop) => stop());
  demoStops = [];
  galleryFor = null;
}

function buildGallery() {
  demoStops.forEach((stop) => stop());
  demoStops = [];
  galleryFor = state.project.id;
  const p = state.project;
  const bg = p.width ? `url("${stripUrl(p.id, [p.duration * 0.3], 240)}")` : "";
  const chosen = $("style-select").value;
  const tiles = Object.entries(state.styles).map(([name, style], i) => {
    const back = el("div", { class: "tile-bg" });
    if (bg) back.style.backgroundImage = bg;
    const cap = el("div", { class: "tile-cap" });
    const tile = el("button", { class: "tile", type: "button", role: "option", "aria-selected": String(name === chosen),
      title: style.description || name, dataset: { name } }, back, cap, el("span", { class: "tile-name" }, name));
    tile.style.animationDelay = `${Math.min(i * 35, 500)}ms`;
    tile.addEventListener("click", () => chooseStyle(name));
    const pos = style.position || "bottom";
    if (pos === "top") { cap.style.top = "10%"; cap.style.bottom = "auto"; }
    else if (pos === "center") { cap.style.top = "40%"; cap.style.bottom = "auto"; }
    demoStops.push(animateDemo(cap, { ...style, name }, () => style.size * tile.clientHeight * 0.95));
    return tile;
  });
  $("gallery").replaceChildren(...tiles);
}

function chooseStyle(name) {
  $("style-select").value = name;
  document.querySelectorAll("#gallery .tile").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.name === name)));
  loadPreview();
}

function highlightTranscript() {
  const t = $("video").currentTime;
  const i = findAt(flatWords, t);
  if (i === lastWord) return;
  if (lastWord >= 0 && flatWords[lastWord]) flatWords[lastWord].span.classList.remove("on");
  document.querySelector("#lines .line.now")?.classList.remove("now");
  lastWord = i;
  if (i < 0) return;
  const { span } = flatWords[i];
  span.classList.add("on");
  const line = span.closest(".line");
  line.classList.add("now");
  if (!$("video").paused) line.scrollIntoView({ block: "nearest" });
}

let raf = 0;
function tick() {
  drawOverlay();
  highlightTranscript();
  raf = $("video").paused ? 0 : requestAnimationFrame(tick);
}
function kick() { if (!raf) raf = requestAnimationFrame(tick); }

let previewSeq = 0;
async function loadPreview() {
  const seq = ++previewSeq;
  try {
    const got = await api(`/api/projects/${state.project.id}/preview?style=${encodeURIComponent($("style-select").value)}`
      + `&emphasis=${encodeURIComponent($("emphasis").value)}`);
    if (seq !== previewSeq) return; // a newer style was picked while this one loaded
    chunks = got;
  } catch (e) {
    if (seq !== previewSeq) return;
    chunks = [];
    toast(e.message, true);
  }
  kick();
}

// --- actions ------------------------------------------------------------------
async function save() {
  try {
    state.transcript = await api(`/api/projects/${state.project.id}/transcript`, { method: "PUT", body: state.transcript });
    setDirty(false);
    toast("Saved. SRT and VTT updated.");
    await loadPreview();
  } catch (e) {
    toast(e.message, true);
    throw e;
  }
}

function renderDownloads() {
  const p = state.project;
  const links = [["captions.srt", "SRT"], ["captions.vtt", "VTT"], ["transcript.json", "JSON"]]
    .map(([f, label]) => el("a", { href: mediaUrl(p.id, f), download: f }, label));
  for (const f of p.burned) links.push(el("a", { href: mediaUrl(p.id, f), download: f }, f));
  $("downloads").replaceChildren(...links);
}

/** Reload the transcript after a change made elsewhere (e.g. the AI's fixes). */
export async function refreshTranscript() {
  state.transcript = null;
  state.transcriptFor = null;
  if (state.tab === "captions") await showCaptions();
}

export async function showCaptions() {
  const p = state.project;
  const select = $("style-select");
  if (!select.options.length) {
    for (const [name, s] of Object.entries(state.styles)) select.append(el("option", { value: name, title: s.description }, name));
  }
  $("cap-empty").hidden = p.has_transcript;
  $("cap-editor").hidden = !p.has_transcript;
  if (!p.has_transcript) { chunks = []; kick(); return; }
  if (galleryFor !== p.id) { buildGallery(); galleryFor = p.id; }
  if (!state.transcript || state.transcriptFor !== p.id) {
    if (dirty) setDirty(false); // edits belonged to another project and were confirmed away
    state.transcript = await api(`/api/projects/${p.id}/transcript`);
    state.transcriptFor = p.id;
    setDirty(false);
    renderLines();
  }
  renderDownloads();
  await loadPreview();
}

export function initCaptions() {
  const v = $("video");
  ["play", "seeked", "timeupdate", "loadedmetadata"].forEach((e) => v.addEventListener(e, kick));
  window.addEventListener("resize", kick);
  $("style-select").addEventListener("change", () => chooseStyle($("style-select").value));
  $("emphasis").addEventListener("change", loadPreview);

  $("lines").addEventListener("click", (e) => {
    const w = e.target.closest(".wd");
    if (w && !w.parentElement.isContentEditable) {
      const word = state.transcript.segments[+w.dataset.si].words[+w.dataset.wi];
      seek(word.start);
    }
  });
  $("lines").addEventListener("dblclick", (e) => {
    const text = e.target.closest(".text");
    if (text) { e.preventDefault(); $("video").pause(); editLine(text); }
  });
  $("save").addEventListener("click", () => save().catch(() => {}));
  window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });

  $("replace-all").addEventListener("click", async () => {
    const find = $("find-text").value.trim(), repl = $("replace-text").value.trim();
    if (!find || !repl) return toast("Fill in both boxes.", true);
    try {
      if (dirty) await save();
      const before = JSON.stringify(state.transcript);
      state.transcript = await api(`/api/projects/${state.project.id}/replace`, { method: "POST", body: { pairs: [`${find}=${repl}`] } });
      renderLines();
      await loadPreview();
      toast(before === JSON.stringify(state.transcript) ? `No whole-word "${find}" found.` : `Replaced "${find}" with "${repl}".`);
    } catch (e) { toast(e.message, true); }
  });

  $("tx-go").addEventListener("click", () => {
    const replace = $("tx-replace").value.split("\n").map((s) => s.trim()).filter(Boolean);
    runJob(api(`/api/projects/${state.project.id}/transcribe`, { method: "POST",
      body: { language: $("tx-language").value.trim(), prompt: $("tx-prompt").value.trim(), replace } }),
    async (r) => {
      toast(`Transcribed ${r.words} words (${r.language}).`);
      state.transcript = null;
      await reloadProject();
      await showCaptions();
    });
  });

  $("burn").addEventListener("click", async () => {
    try { if (dirty) await save(); } catch { return; }
    runJob(api(`/api/projects/${state.project.id}/burn`, { method: "POST",
      body: { style: $("style-select").value, emphasis: $("emphasis").value } }),
      async (r) => {
        await reloadProject();
        renderDownloads();
        confetti();
        toast(`Done: ${r.file}. Download it above, or open the folder.`);
      });
  });
}
