// Clips tab: find candidate Shorts, let an AI judge them, tweak, render 9:16.
import { api, el, fmt, mediaUrl, reloadProject, runJob, skeletons, stagger, state, stripUrl, toast } from "/static/app.js";
import { animateDemo } from "/static/captionfx.js";
import { confetti } from "/static/fx.js";
import { flashSpan, setSpans } from "/static/timeline.js";

const $ = (id) => document.getElementById(id);
let cands = [];            // [{id, start, end, title, score, source, reason, sentences, selected}]
let candsFor = null;
let previewEnd = null;
let renderedAt = Date.now();
let phoneStop = () => {};

const textOf = (c) => c.sentences.map((s) => s.text).join(" ");
const defaultTitle = (c) => c.title || (c.sentences[0]?.text || "").slice(0, 70);

function fromServer(list, selectTop = 5) {
  return list.map((c, i) => ({ ...c, title: c.title || "", selected: i < selectTop }));
}

/** The judge's picks first (ticked, with their titles and times), then the rest unticked. */
function withPicks(list, picked) {
  const ids = new Set(picked.map((c) => c.id));
  const rest = list.filter((c) => !ids.has(c.id)).map((c) => ({ ...c, selected: false }));
  return [...picked.map((c) => ({ ...c, selected: true })), ...rest];
}

function updateRenderBar() {
  const n = cands.filter((c) => c.selected).length;
  $("render-bar").hidden = !cands.length;
  $("render").textContent = `Render ${n} clip${n === 1 ? "" : "s"}`;
  $("render").disabled = !n;
  setSpans(cands.map((c) => ({ id: c.id, start: c.start, end: c.end, selected: c.selected })));
  drawPhone();
}

function patch(id, changes) {
  cands = cands.map((c) => (c.id === id ? { ...c, ...changes } : c));
}

function timeInput(c, key) {
  const input = el("input", { type: "number", step: "0.1", min: "0", value: c[key].toFixed(1), "aria-label": key });
  input.addEventListener("change", () => {
    const v = parseFloat(input.value);
    if (Number.isFinite(v)) patch(c.id, { [key]: v });
    renderCands();
  });
  const now = el("button", { class: "btn small", type: "button", title: `Set ${key} to the player's position`,
    onclick: () => { patch(c.id, { [key]: +$("video").currentTime.toFixed(2) }); renderCands(); } }, "← now");
  return [el("span", {}, key), input, now];
}

function filmstrip(c) {
  const p = state.project;
  if (!p.width) return null;
  const d = c.end - c.start;
  const img = el("img", { alt: "", loading: "lazy", src: stripUrl(p.id, [c.start + 0.5, c.start + d / 2, c.end - 1], 90) });
  img.addEventListener("error", () => img.parentElement?.remove());
  return el("div", { class: "cand-strip", title: "Preview", onclick: () => preview(c) }, img);
}

function card(c, rank) {
  const check = el("input", { type: "checkbox", "aria-label": "Render this clip" });
  check.checked = c.selected;
  check.addEventListener("change", () => { patch(c.id, { selected: check.checked }); renderCands(false); });
  const title = el("input", { type: "text", class: "cand-title", value: defaultTitle(c), "aria-label": "Clip title" });
  title.addEventListener("change", () => { patch(c.id, { title: title.value.trim() }); drawPhone(); });
  const text = el("div", { class: "cand-text", title: "Click to show all" }, textOf(c));
  text.addEventListener("click", () => text.classList.toggle("open"));
  const score = c.source === "judge"
    ? el("span", { class: "badge judge" }, `AI pick ${c.score}/10`)
    : el("span", { class: "badge" }, `score ${Math.round(c.score * 100)}`);
  const dur = c.end - c.start;
  return el("div", { class: `card cand${c.selected ? " sel" : ""}`, dataset: { id: c.id } },
    check,
    el("div", {},
      filmstrip(c),
      title,
      el("div", { class: "cand-meta" },
        el("span", {}, `#${rank}`), score,
        el("span", {}, `${fmt(c.start)}–${fmt(c.end)} · ${dur.toFixed(0)} s`),
        el("button", { class: "btn small", type: "button", onclick: () => preview(c) }, "▶ Preview")),
      text,
      c.reason ? el("div", { class: "cand-reason" }, c.reason) : null,
      el("div", { class: "times" }, ...timeInput(c, "start"), ...timeInput(c, "end"))));
}

function renderCands(animate = true) {
  const nodes = cands.map((c, i) => card(c, i + 1));
  if (animate) stagger(nodes, 30);
  else nodes.forEach((n) => { n.style.animation = "none"; });
  $("cands").replaceChildren(...nodes);
  updateRenderBar();
}

function renderShorts() {
  const p = state.project;
  $("rendered-title").hidden = !p.clips.length;
  $("rendered").replaceChildren(...stagger(p.clips.map((m) => {
    const src = `${mediaUrl(p.id, `clips/${m.file}`)}&v=${renderedAt}`;
    const video = el("video", { src, controls: true, preload: "metadata", playsinline: true });
    video.muted = true;
    video.addEventListener("mouseenter", () => video.paused && video.play().catch(() => {}));
    video.addEventListener("mouseleave", () => { if (video.muted) video.pause(); });
    return el("div", { class: "short" },
      video,
      el("div", { class: "short-title" }, m.title || m.file),
      el("div", { class: "muted small" }, `${m.duration.toFixed(0)} s · ${m.layout}${m.style ? ` · ${m.style}` : ""}`),
      el("a", { href: src, download: m.file, class: "small" }, "Download"));
  }), 70));
}

function preview(c) {
  const v = $("video");
  previewEnd = c.end;
  flashSpan(c.id);
  v.currentTime = c.start;
  v.play().catch(() => {});
}

// --- the little phone that previews a Short's title look and caption style --------
let phoneKey = "";

export function stopPhone() {
  phoneStop();
  phoneStop = () => {};
  phoneKey = "";
}
function drawPhone() {
  const first = cands.find((c) => c.selected);
  const look = $("title-look").value || "banner";
  const styleName = $("clip-style").value;
  const style = state.styles[styleName];
  const key = `${first?.id}|${first?.title}|${look}|${styleName}`;
  if (!first || !style || key === phoneKey) return;
  phoneKey = key;
  const p = state.project;
  if (p.width) $("phone-bg").style.backgroundImage = `url("${stripUrl(p.id, [(first.start + first.end) / 2], 240)}")`;
  const titleBox = $("phone-title");
  titleBox.className = `phone-title ${look}`;
  titleBox.replaceChildren(look === "none" ? "" : el("span", {}, (first.title || defaultTitle(first)).toUpperCase()));
  void titleBox.offsetWidth; // restart the entrance animation
  titleBox.classList.add("go");
  phoneStop();
  const cap = $("phone-cap");
  phoneStop = animateDemo(cap, style, () => style.size * $("phone").clientWidth * 0.95);
}

export async function showClips() {
  const p = state.project;
  $("clips-needs-transcript").hidden = p.has_transcript;
  $("clips-body").hidden = !p.has_transcript;
  const sel = $("clip-style");
  if (!sel.options.length) for (const name of Object.keys(state.styles)) sel.append(el("option", { value: name }, name));
  const looks = $("title-look");
  if (!looks.options.length) {
    for (const name of state.titleLooks) looks.append(el("option", { value: name }, name));
    looks.value = "banner";
  }
  if (candsFor !== p.id) {
    cands = p.picks.length ? withPicks(fromServer(p.candidates, 0), p.picks) : fromServer(p.candidates);
    candsFor = p.id;
    phoneKey = "";
  }
  renderCands();
  renderShorts();
}

/** For the AI panel: the ticked clips with their current (maybe edited) times. */
export function tickedClips() {
  return cands.filter((c) => c.selected).map((c) => ({ id: c.id, start: c.start, end: c.end }));
}

/** The AI's picks go first, ticked, with their titles and times. */
export function applyPicks(picked) {
  cands = withPicks(cands, picked);
  phoneKey = "";
  if (state.tab === "clips") renderCands();
}

/** New hook titles from the AI for some clips. */
export function applyTitles(list) {
  const byId = new Map(list.map((t) => [t.id, t.title]));
  cands = cands.map((c) => (byId.has(c.id) ? { ...c, title: byId.get(c.id) } : c));
  phoneKey = "";
  if (state.tab === "clips") renderCands(false);
  document.querySelectorAll(".cand").forEach((n) => {
    if (byId.has(+n.dataset.id)) n.querySelector(".cand-title")?.animate(
      [{ boxShadow: "0 0 0 3px rgba(124,108,255,.9)" }, { boxShadow: "0 0 0 0 rgba(124,108,255,0)" }], { duration: 1200 });
  });
}

export function initClips() {
  $("video").addEventListener("timeupdate", () => {
    const v = $("video");
    if (previewEnd !== null && v.currentTime >= previewEnd) { v.pause(); previewEnd = null; flashSpan(null); }
  });
  $("video").addEventListener("seeking", () => {
    // a manual seek outside the clip cancels the preview stop
    if (previewEnd !== null && $("video").currentTime > previewEnd) { previewEnd = null; flashSpan(null); }
  });
  document.addEventListener("job-start", (e) => {
    if (e.detail.kind === "find" && e.detail.project === state.project?.id) $("cands").replaceChildren(...skeletons("cand-skel", 4));
  });
  document.addEventListener("job-end", (e) => {
    if (e.detail.kind === "find" && e.detail.status !== "done" && state.tab === "clips") renderCands();
  });
  ["title-look", "clip-style"].forEach((id) => $(id).addEventListener("change", () => { phoneKey = ""; drawPhone(); }));

  $("find").addEventListener("click", () => {
    const min = parseFloat($("min-len").value), max = parseFloat($("max-len").value);
    const pid = state.project.id;
    runJob(api(`/api/projects/${pid}/find`, { method: "POST", body: { min, max } }), async (list) => {
      cands = fromServer(list);
      candsFor = pid;
      phoneKey = "";
      renderCands();
      toast(`Found ${list.length} candidates. The top 5 are ticked. Want an AI to pick? Press ✦ Pick with AI.`);
      $("ai-pick").animate([{ transform: "scale(1)" }, { transform: "scale(1.12)" }, { transform: "scale(1)" }],
        { duration: 600, iterations: 2 });
    });
  });

  $("render").addEventListener("click", () => {
    const clips = cands.filter((c) => c.selected)
      .map((c) => ({ id: c.id, start: c.start, end: c.end, title: c.title || defaultTitle(c), score: c.score, reason: c.reason }));
    runJob(api(`/api/projects/${state.project.id}/render`, { method: "POST",
      body: { clips, style: $("clip-style").value, layout: $("layout-select").value, emphasis: $("clip-emphasis").value,
        title_look: $("title-look").value, title_secs: Number.isFinite(parseFloat($("title-secs").value)) ? parseFloat($("title-secs").value) : 3.5 } }),
    async (manifest) => {
      renderedAt = Date.now();
      await reloadProject();
      renderShorts();
      $("rendered-title").scrollIntoView({ behavior: "smooth" });
      confetti();
      toast(`Rendered ${manifest.length} Shorts.`);
    });
  });
}
