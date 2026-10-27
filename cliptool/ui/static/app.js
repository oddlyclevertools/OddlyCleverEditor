// cliptool UI: core (API, jobs, home, routing). Captions and clips live in their own modules.
import { discardEdits, hasUnsavedEdits, initCaptions, showCaptions, stopGallery } from "/static/captions.js";
import { initClips, showClips, stopPhone } from "/static/clips.js";
import { closeAi, initAi, openAi } from "/static/ai.js";
import { movePill, transition } from "/static/fx.js";
import { initTimeline, setSpans, setWave } from "/static/timeline.js";

const TOKEN = document.querySelector('meta[name="cliptool-token"]').content;
const $ = (id) => document.getElementById(id);

export const state = { styles: {}, titleLooks: [], project: null, transcript: null, tab: "captions" };

// --- helpers -------------------------------------------------------------------
export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { "X-Cliptool-Token": TOKEN, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export function mediaUrl(pid, rel) {
  const base = rel ? `/media/${pid}/file/${rel.split("/").map(encodeURIComponent).join("/")}` : `/media/${pid}/source`;
  return `${base}?t=${encodeURIComponent(TOKEN)}`;
}

/** Frames of the source at the given times, side by side (decoded on the GPU, cached). */
export function stripUrl(pid, times, height = 120) {
  return `/media/${pid}/strip?at=${times.map((t) => t.toFixed(2)).join(",")}&h=${height}&t=${encodeURIComponent(TOKEN)}`;
}

let toastTimer;
export function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), isError ? 7000 : 3200);
}

export const fmt = (s) => {
  s = Math.max(0, s);
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
  return node;
}

/** Stagger the entrance animation of a list's children. */
export function stagger(nodes, stepMs = 40) {
  nodes.forEach((n, i) => { n.style.animationDelay = `${Math.min(i * stepMs, 600)}ms`; });
  return nodes;
}

export const skeletons = (cls, n) => Array.from({ length: n }, () => el("div", { class: `skel ${cls}` }));

// --- jobs (one GPU job at a time, polled) --------------------------------------
const watching = new Map(); // job id -> latest state
const started = new Map();  // job id -> {t, p} when we first saw progress

async function cancelCurrentJob() {
  const jobs = [...watching.values()];
  const current = jobs.find((j) => j.status === "running") || jobs[0];
  if (!current) return;
  try {
    await api(`/api/jobs/${current.id}/cancel`, { method: "POST", body: {} });
    toast("Cancelling…");
  } catch (e) {
    toast(e.message, true);
  }
}

function eta(job) {
  const p = job.progress || 0;
  if (!started.has(job.id) && p > 0) started.set(job.id, { t: performance.now(), p });
  const s = started.get(job.id);
  if (!s || p - s.p < 0.03) return "";
  const elapsed = (performance.now() - s.t) / 1000;
  const left = (elapsed * (1 - p)) / (p - s.p);
  if (!Number.isFinite(left) || elapsed < 2) return "";
  return left < 60 ? `~${Math.ceil(left)} s left` : `~${Math.ceil(left / 60)} min left`;
}

function drawJobBar() {
  const jobs = [...watching.values()];
  $("jobbar").hidden = !jobs.length;
  if (!jobs.length) return;
  const current = jobs.find((j) => j.status === "running") || jobs[0];
  const queued = jobs.length - 1;
  $("job-msg").textContent = `${current.message || current.kind}${queued ? ` (+${queued} queued)` : ""}`;
  $("job-fill").style.width = `${Math.round(current.progress * 100)}%`;
  $("job-pct").textContent = `${Math.round(current.progress * 100)}%`;
  $("job-eta").textContent = eta(current);
}

export const jobRunning = (kind) => [...watching.values()].some((j) => j.kind === kind && j.project === state.project?.id);

/** Poll a job; onDone runs only if the user is still on the project that started it. */
export async function runJob(promise, onDone) {
  const pid = state.project?.id;
  let job;
  try {
    job = await promise;
  } catch (e) {
    toast(e.message, true);
    return;
  }
  if (watching.has(job.id)) return;
  document.dispatchEvent(new CustomEvent("job-start", { detail: job }));
  try {
    for (;;) {
      watching.set(job.id, job);
      drawJobBar();
      if (job.status === "done") {
        if (onDone && state.project?.id === (job.project || pid)) await onDone(job.result);
        else toast(`A ${job.kind} job finished for another project. Open it to see the result.`);
        break;
      }
      if (job.status === "error") {
        toast(job.error || "Job failed", true);
        break;
      }
      await new Promise((r) => setTimeout(r, 600));
      job = await api(`/api/jobs/${job.id}`);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    watching.delete(job.id);
    started.delete(job.id);
    drawJobBar();
    document.dispatchEvent(new CustomEvent("job-end", { detail: job }));
  }
}

// --- home ------------------------------------------------------------------------
function projectCard(p) {
  const img = el("img", { alt: "", loading: "lazy", src: stripUrl(p.id, [8], 180) });
  img.addEventListener("error", () => img.remove()); // audio-only projects have no frames
  return el("button", { class: "card proj", type: "button", onclick: () => (location.hash = `#/p/${p.id}`) },
    el("div", { class: "thumb" }, img),
    el("div", { class: "meta" },
      el("span", { class: "proj-name" }, p.name),
      el("span", { class: "badges" },
        el("span", { class: `badge ${p.has_transcript ? "ok" : ""}` }, p.has_transcript ? "transcribed" : "not transcribed"),
        p.clips ? el("span", { class: "badge judge" }, "Shorts rendered") : null),
      el("span", { class: "muted small" }, new Date(p.created * 1000).toLocaleString())));
}

async function loadHome() {
  const list = $("projects");
  if (!list.children.length) list.replaceChildren(...skeletons("proj-skel", 3));
  const s = await api("/api/state");
  state.styles = s.styles;
  state.titleLooks = s.title_looks || [];
  $("workspace-note").textContent = `Workspace: ${s.workspace}`;
  list.replaceChildren(...(s.projects.length
    ? stagger(s.projects.map(projectCard), 60)
    : [el("p", { class: "muted" }, "No projects yet. Drop a video above to start.")]));
  return s;
}

const UPLOAD_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts", ".mp3", ".wav", ".m4a"];
const RING = 119.4; // circumference of the upload ring (r = 19)

function upload(file) {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!UPLOAD_EXTS.includes(ext)) return toast(`Not a supported video or audio file: ${file.name}`, true);
  const drop = $("drop"), box = $("upload"), ring = $("upload-ring");
  drop.classList.remove("dropped");
  void drop.offsetWidth; // restart the "gulp" animation
  drop.classList.add("dropped");
  box.hidden = false;
  ring.style.strokeDashoffset = String(RING);
  $("upload-msg").textContent = `Copying ${file.name}…`;
  const xhr = new XMLHttpRequest();
  xhr.open("PUT", `/api/upload?name=${encodeURIComponent(file.name)}`);
  xhr.setRequestHeader("X-Cliptool-Token", TOKEN);
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const f = e.loaded / e.total;
    ring.style.strokeDashoffset = String(RING * (1 - f));
    $("upload-msg").textContent = `Copying ${file.name}… ${Math.round(f * 100)}%`;
  };
  xhr.onload = () => {
    box.hidden = true;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* handled below */ }
    if (xhr.status !== 200) return toast(data.error || `Upload failed (${xhr.status})`, true);
    location.hash = `#/p/${data.id}`;
  };
  xhr.onerror = () => {
    box.hidden = true;
    toast("Upload failed. If OddlyCleverEditor was restarted, reload the page and try again.", true);
  };
  xhr.send(file);
}

function initHome() {
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) upload(file);
  });
  // stop the browser opening a file dropped anywhere else on the page
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  $("file-input").addEventListener("change", (e) => e.target.files[0] && upload(e.target.files[0]));
  $("open-path").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const p = await api("/api/projects", { method: "POST", body: { path: $("path-input").value } });
      location.hash = `#/p/${p.id}`;
    } catch (err) {
      toast(err.message, true);
    }
  });
}

// --- project ----------------------------------------------------------------------
export async function reloadProject() {
  state.project = await api(`/api/projects/${state.project.id}`);
  return state.project;
}

async function openProject(pid) {
  if (!Object.keys(state.styles).length) await loadHome();
  const fresh = state.project?.id !== pid;
  state.project = await api(`/api/projects/${pid}`);
  const p = state.project;
  await transition(() => {
    $("proj-name").textContent = p.name;
    const video = $("video");
    if (!video.src.includes(`/media/${pid}/`)) video.src = mediaUrl(pid);
    $("player-info").textContent =
      `${p.width}×${p.height} · ${p.fps.toFixed(2)} fps · ${fmt(p.duration)}${p.uploaded ? "" : ` · ${p.source}`}`;
    $("view-home").hidden = true;
    $("view-project").hidden = false;
  });
  if (fresh) {
    setWave([], p.duration);
    setSpans([]);
    api(`/api/projects/${pid}/waveform`).then((peaks) => {
      if (state.project?.id === pid) setWave(peaks, p.duration);
    }).catch(() => {});
  }
  await showTab(state.tab);
  // resume progress display for a job started before a reload
  const s = await api("/api/state");
  for (const j of s.jobs.filter((j) => j.project === pid)) runJob(Promise.resolve(j), () => showTab(state.tab));
}

async function showTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  movePill($("tab-pill"), document.querySelector(`.tabs button[data-tab="${tab}"]`));
  $("tab-captions").hidden = tab !== "captions";
  $("tab-clips").hidden = tab !== "clips";
  if (tab !== "captions") $("overlay").replaceChildren(); // the caption preview belongs to the Captions tab
  if (tab !== "clips") { setSpans([]); stopPhone(); }
  if (tab !== "captions") stopGallery();
  try {
    if (tab === "captions") await showCaptions();
    else await showClips();
  } catch (e) {
    toast(e.message, true);
  }
}

let currentHash = location.hash;
async function route() {
  const m = location.hash.match(/^#\/p\/([\w-]+)/);
  const leaving = state.project && (!m || m[1] !== state.project.id) && !$("view-project").hidden;
  if (leaving && hasUnsavedEdits()) {
    if (!confirm("You have unsaved transcript edits. Leave without saving?")) {
      history.replaceState(null, "", currentHash); // stay put (replaceState fires no hashchange)
      return;
    }
    discardEdits();
  }
  currentHash = location.hash;
  try {
    if (m) return await openProject(m[1]);
    $("video").pause();
    closeAi();
    stopGallery();
    stopPhone();
    await transition(() => {
      $("view-project").hidden = true;
      $("view-home").hidden = false;
    });
    await loadHome();
  } catch (e) {
    toast(e.message, true);
    if (m) location.hash = "";
  }
}

function init() {
  initHome();
  initTimeline();
  initCaptions();
  initClips();
  initAi();
  $("ai-pick").addEventListener("click", () => openAi("judge"));
  $("ai-titles").addEventListener("click", () => openAi("titles"));
  $("ai-fixes").addEventListener("click", () => openAi("fixes"));
  $("home-link").addEventListener("click", () => (location.hash = ""));
  $("job-cancel").addEventListener("click", cancelCurrentJob);
  $("back").addEventListener("click", () => (location.hash = ""));
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  window.addEventListener("resize", () => movePill($("tab-pill"), document.querySelector(".tabs button[aria-selected=true]")));
  $("reveal").addEventListener("click", () =>
    api(`/api/projects/${state.project.id}/reveal`, { method: "POST", body: { sub: state.tab === "clips" ? "clips" : "" } })
      .catch((e) => toast(e.message, true)));
  window.addEventListener("hashchange", route);
  route();
}

init();
