// The strip under the player: the video's waveform, candidate clips as spans, a playhead.
const $ = (id) => document.getElementById(id);

let peaks = [];
let duration = 0;
let spans = [];   // [{start, end, selected, id}]
let raf = 0;

function draw() {
  const canvas = $("wave");
  const dpr = devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (!peaks.length) return;
  const grad = ctx.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, "rgba(124,108,255,.85)");
  grad.addColorStop(0.5, "rgba(255,90,138,.85)");
  grad.addColorStop(1, "rgba(255,216,77,.85)");
  ctx.fillStyle = grad;
  const bars = Math.max(1, Math.floor(w / 3));
  const mid = h / 2;
  for (let i = 0; i < bars; i++) {
    const a = Math.floor((i / bars) * peaks.length), b = Math.max(a + 1, Math.floor(((i + 1) / bars) * peaks.length));
    let p = 0;
    for (let j = a; j < b; j++) p = Math.max(p, peaks[j] || 0);
    const bh = Math.max(1.5, p * (h - 10));
    ctx.globalAlpha = 0.35 + 0.65 * p;
    ctx.beginPath();
    ctx.roundRect(i * 3, mid - bh / 2, 2, bh, 1);
    ctx.fill();
  }
}

function drawSpans() {
  const box = $("spans");
  if (!duration) return box.replaceChildren();
  box.replaceChildren(...spans.map((s, i) => {
    const d = document.createElement("div");
    d.className = `span${s.selected ? " sel" : ""}`;
    d.style.left = `${(s.start / duration) * 100}%`;
    d.style.width = `${Math.max(0.3, ((s.end - s.start) / duration) * 100)}%`;
    d.style.animationDelay = `${Math.min(i * 25, 600)}ms`;
    d.dataset.id = s.id;
    return d;
  }));
}

function playhead() {
  const v = $("video");
  if (duration) $("playhead").style.left = `${(v.currentTime / duration) * 100}%`;
  raf = v.paused ? 0 : requestAnimationFrame(playhead);
}

export function kickPlayhead() { if (!raf) raf = requestAnimationFrame(playhead); }

/** New project: its duration and loudness peaks (0-1). */
export function setWave(newPeaks, newDuration) {
  peaks = newPeaks || [];
  duration = newDuration || 0;
  draw();
  drawSpans();
  kickPlayhead();
}

export function setSpans(list) {
  spans = list;
  drawSpans();
}

/** Briefly highlight one span (e.g. while previewing that clip). */
export function flashSpan(id) {
  document.querySelectorAll("#spans .span").forEach((s) => s.classList.toggle("hot", s.dataset.id === String(id)));
}

export function initTimeline() {
  const tl = $("timeline"), tip = $("hover-time"), v = $("video");
  const at = (e) => {
    const r = tl.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  };
  const label = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  tl.addEventListener("click", (e) => {
    if (!duration) return;
    v.currentTime = at(e) * duration;
    kickPlayhead();
  });
  tl.addEventListener("mousemove", (e) => {
    if (!duration) return;
    tip.hidden = false;
    tip.style.left = `${at(e) * 100}%`;
    tip.textContent = label(at(e) * duration);
  });
  tl.addEventListener("mouseleave", () => { tip.hidden = true; });
  ["play", "seeked", "timeupdate"].forEach((t) => v.addEventListener(t, kickPlayhead));
  new ResizeObserver(draw).observe(tl);
}
