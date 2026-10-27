// The AI panel: use any AI for three jobs (pick clips, write hook titles, find transcript mistakes),
// three ways (ask an AI server, paste into a chat, or let an agent like Claude Code work from files).
import { api, el, runJob, state, toast } from "/static/app.js";
import { hasUnsavedEdits, refreshTranscript } from "/static/captions.js";
import { applyPicks, applyTitles, tickedClips } from "/static/clips.js";

const $ = (id) => document.getElementById(id);

// Address presets for well-known OpenAI-compatible endpoints. Model names change often, so they're
// only placeholders: type the exact name your provider lists.
const PROVIDERS = [
  { id: "ollama", name: "Ollama (on this PC)", url: "http://127.0.0.1:11434/v1", hint: "e.g. qwen3:8b" },
  { id: "lmstudio", name: "LM Studio (on this PC)", url: "http://127.0.0.1:1234/v1", hint: "the model you loaded" },
  { id: "llamacpp", name: "llama.cpp server (on this PC)", url: "http://127.0.0.1:8080/v1", hint: "any name works" },
  { id: "anthropic", name: "Anthropic Claude", url: "https://api.anthropic.com/v1", hint: "e.g. claude-sonnet-5-5" },
  { id: "openai", name: "OpenAI", url: "https://api.openai.com/v1", hint: "a model from your OpenAI account" },
  { id: "gemini", name: "Google Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai", hint: "a Gemini model name" },
  { id: "openrouter", name: "OpenRouter (hundreds of models)", url: "https://openrouter.ai/api/v1", hint: "provider/model" },
  { id: "groq", name: "Groq", url: "https://api.groq.com/openai/v1", hint: "a Groq model name" },
  { id: "mistral", name: "Mistral", url: "https://api.mistral.ai/v1", hint: "a Mistral model name" },
  { id: "deepseek", name: "DeepSeek", url: "https://api.deepseek.com/v1", hint: "a DeepSeek model name" },
  { id: "custom", name: "Other (any OpenAI-compatible API)", url: "", hint: "model name" },
];

const TASKS = {
  judge: { label: "Pick the best clips", needs: "candidates", done: "Applied the AI's picks." },
  titles: { label: "Write hook titles", needs: "ticked", done: "New hook titles are on the ticked clips." },
  fixes: { label: "Find transcript mistakes", needs: "transcript", done: "" },
};
let task = "judge";
let key = ""; // the API key lives only in this variable: never stored, cleared on reload

const isLocal = (url) => {
  try { return ["127.0.0.1", "localhost", "[::1]"].includes(new URL(url).hostname); } catch { return false; }
};

function remember(id) {
  try { $(id).value = localStorage.getItem(`cliptool.ai.${id}`) || $(id).value; } catch { /* storage blocked */ }
  $(id).addEventListener("change", () => { try { localStorage.setItem(`cliptool.ai.${id}`, $(id).value.trim()); } catch { /* */ } });
}

function privacyNote() {
  const url = $("ai-url").value.trim();
  const note = $("ai-privacy");
  if (!url) { note.textContent = ""; return; }
  let host = url;
  try { host = new URL(url).hostname; } catch { /* shown as typed */ }
  note.className = `ai-privacy small ${isLocal(url) ? "ok" : "warn"}`;
  note.textContent = isLocal(url)
    ? "On this PC: nothing leaves your computer."
    : `Cloud: when you press Ask, the transcript text is sent to ${host}.`;
}

function chooseProvider(id, keepUrl = false) {
  const p = PROVIDERS.find((x) => x.id === id) || PROVIDERS[0];
  if (!keepUrl && p.url) $("ai-url").value = p.url;
  $("ai-model").placeholder = p.hint;
  $("ai-key-row").hidden = id === "ollama" || id === "lmstudio" || id === "llamacpp";
  privacyNote();
}

function chooseTask(name) {
  task = name;
  document.querySelectorAll("#ai-tasks .ai-task").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.task === name)));
  $("ai-count-row").hidden = name !== "judge";
  $("ai-reply").placeholder = name === "fixes"
    ? 'Paste the reply, e.g. [{"find": "Callan", "replace": "Callum"}]'
    : name === "titles" ? 'Paste the reply, e.g. [{"id": 7, "title": "..."}]'
      : 'Paste the reply, e.g. [{"id": 7, "score": 9, "title": "..."}]';
  $("ai-results").replaceChildren();
}

/** What this task sends along: the count to pick, or the ticked clips with their (edited) times. */
function payload() {
  const body = { count: parseInt($("ai-count").value, 10) || 5 };
  if (task === "titles") {
    body.clips = tickedClips();
    if (!body.clips.length) throw new Error("Tick at least one clip on the Clips tab first.");
  }
  if (task === "fixes" && !state.project.has_transcript) throw new Error("Transcribe the video first.");
  if (task === "judge" && !state.project.candidates?.length) throw new Error("Press Find clips first (Clips tab).");
  return body;
}

async function handle(result) {
  if (task === "judge") {
    applyPicks(result);
    toast(`${TASKS.judge.done} (${result.length})`);
  } else if (task === "titles") {
    applyTitles(result);
    toast(TASKS.titles.done);
  } else {
    showFixes(result);
  }
}

function showFixes(fixes) {
  const box = $("ai-results");
  if (!fixes.length) {
    box.replaceChildren(el("p", { class: "muted small" }, "The AI found nothing to fix."));
    return;
  }
  const apply = async (pairs, buttons) => {
    if (hasUnsavedEdits()) return toast("Save your transcript edits first (Captions tab).", true);
    try {
      await api(`/api/projects/${state.project.id}/replace`, { method: "POST", body: { pairs } });
      buttons.forEach((b) => { b.disabled = true; b.textContent = "Applied"; b.closest(".fix")?.classList.add("done"); });
      await refreshTranscript();
      toast(`Fixed ${pairs.length} word${pairs.length === 1 ? "" : "s"}. SRT and VTT updated.`);
    } catch (e) { toast(e.message, true); }
  };
  const rows = fixes.map((f, i) => {
    const btn = el("button", { class: "btn small", type: "button" }, "Apply");
    btn.addEventListener("click", () => apply([`${f.find}=${f.replace}`], [btn]));
    const row = el("div", { class: "fix" },
      el("div", {}, el("s", {}, f.find), " → ", el("b", {}, f.replace), el("span", { class: "muted small" }, ` ×${f.count}`)),
      f.why ? el("div", { class: "muted small" }, f.why) : null,
      btn);
    row.style.animationDelay = `${Math.min(i * 40, 600)}ms`;
    return row;
  });
  const all = el("button", { class: "btn primary small", type: "button" }, `Apply all ${fixes.length}`);
  all.addEventListener("click", () => {
    const open = [...box.querySelectorAll(".fix:not(.done) button")];
    const pairs = fixes.filter((_, i) => !rows[i].classList.contains("done")).map((f) => `${f.find}=${f.replace}`);
    if (pairs.length) apply(pairs, [...open, all]);
  });
  box.replaceChildren(el("div", { class: "row" }, el("b", {}, `${fixes.length} suggested fixes`), all), ...rows);
}

async function copyText(text) {
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 2000));
  try { await Promise.race([navigator.clipboard.writeText(text), timeout]); return true; } catch { return false; }
}

function showFallback(text, message) {
  const box = $("ai-prompt-box");
  box.value = text;
  box.hidden = false;
  box.focus();
  box.select();
  toast(message);
}

async function getPrompt() {
  return api(`/api/projects/${state.project.id}/ai/${task}/prompt`, { method: "POST", body: payload() });
}

export function openAi(which) {
  if (which) chooseTask(which);
  $("ai-drawer").hidden = false;
  $("ai-backdrop").hidden = false;
  requestAnimationFrame(() => document.body.classList.add("ai-open"));
}

export function closeAi() {
  document.body.classList.remove("ai-open");
  setTimeout(() => {
    if (document.body.classList.contains("ai-open")) return;
    $("ai-drawer").hidden = true;
    $("ai-backdrop").hidden = true;
  }, 320);
}

export function initAi() {
  const sel = $("ai-provider");
  sel.replaceChildren(...PROVIDERS.map((p) => el("option", { value: p.id }, p.name)));
  remember("ai-provider");
  remember("ai-url");
  remember("ai-model");
  chooseProvider(sel.value, Boolean($("ai-url").value));
  sel.addEventListener("change", () => chooseProvider(sel.value));
  $("ai-url").addEventListener("input", privacyNote);
  $("ai-key").addEventListener("input", (e) => { key = e.target.value.trim(); });
  $("ai-tasks").replaceChildren(...Object.entries(TASKS).map(([name, t]) =>
    el("button", { class: "ai-task", type: "button", dataset: { task: name }, onclick: () => chooseTask(name) }, t.label)));
  chooseTask("judge");

  $("ai-open").addEventListener("click", () => openAi());
  $("ai-close").addEventListener("click", closeAi);
  $("ai-backdrop").addEventListener("click", closeAi);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("ai-drawer").hidden) closeAi(); });

  $("ai-ask").addEventListener("click", () => {
    const url = $("ai-url").value.trim(), model = $("ai-model").value.trim();
    if (!url) return toast("Pick a provider or type its address.", true);
    if (!model) return toast("Type the model name your provider uses.", true);
    let body;
    try { body = payload(); } catch (e) { return toast(e.message, true); }
    if (!isLocal(url)) {
      let host = url;
      try { host = new URL(url).hostname; } catch { return toast("That address doesn't look right.", true); }
      if (!confirm(`This sends the transcript text to ${host}. Continue?`)) return;
    }
    runJob(api(`/api/projects/${state.project.id}/ai/${task}/ask`, { method: "POST",
      body: { ...body, url, model, key: key || undefined } }), handle);
  });

  $("ai-copy").addEventListener("click", async () => {
    try {
      const { prompt } = await getPrompt();
      if (await copyText(prompt)) toast("Prompt copied. Paste it into any AI, then paste its reply below.");
      else showFallback(prompt, "Couldn't reach the clipboard. The prompt is selected in the box: press Ctrl+C.");
    } catch (e) { toast(e.message, true); }
  });

  $("ai-agent").addEventListener("click", async () => {
    try {
      const { prompt_path: prompt, reply_path: reply } = await getPrompt();
      const line = `Read "${prompt}" and follow it. Save only the JSON array to "${reply}".`;
      if (await copyText(line)) toast("Copied. Paste it into Claude Code (or any agent), then press Load reply file.");
      else showFallback(line, "Couldn't reach the clipboard. The line is selected in the box: press Ctrl+C.");
    } catch (e) { toast(e.message, true); }
  });

  const apply = async (body) => {
    try {
      const extra = task === "titles" ? { clips: tickedClips() } : {};
      handle(await api(`/api/projects/${state.project.id}/ai/${task}/apply`, { method: "POST",
        body: { ...body, ...extra, count: parseInt($("ai-count").value, 10) || 5 } }));
    } catch (e) { toast(e.message, true); }
  };
  $("ai-apply").addEventListener("click", () => {
    const text = $("ai-reply").value.trim();
    if (!text) return toast("Paste the AI's reply first.", true);
    apply({ text });
  });
  $("ai-load").addEventListener("click", () => apply({ from_file: true }));
}
