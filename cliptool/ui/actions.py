"""What the UI can do to a project. HTTP-free, so it is easy to test."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import ai_tasks, clipfind, judge_llm, media, styles, titles
from .. import transcript as tr
from ..ass import display_text, emphasis_flags, fit_rules
from ..chunking import chunk_words, split_lines
from ..cli import FONTS_DIR, write_subtitles
from ..clips_cmd import render_clips
from .projects import Project, Store

Progress = Callable[[float, str], None]


def paths(store: Store, p: Project) -> dict[str, Path]:
    f = store.folder(p.id)
    return {
        "transcript": f / "transcript.json", "srt": f / "captions.srt", "vtt": f / "captions.vtt",
        "candidates": f / "candidates.json", "judge": f / "judge.md", "clips": f / "clips",
        "picks": f / "picks.json", "waveform": f / "waveform.json", "thumbs": f / "thumbs",
        "ranking": f / "ranking.json",
    }


def detail(store: Store, p: Project) -> dict[str, Any]:
    ps = paths(store, p)
    info = media.probe(Path(p.source))
    clips_manifest = ps["clips"] / "clips.json"
    folder = store.folder(p.id)
    return {
        "id": p.id, "name": p.name, "source": p.source, "uploaded": p.uploaded,
        "width": info.width, "height": info.height, "duration": info.duration, "fps": info.fps,
        "has_transcript": ps["transcript"].is_file(),
        "burned": sorted(x.name for x in folder.glob("captioned_*.mp4")),
        "candidates": json.loads(ps["candidates"].read_text(encoding="utf-8")) if ps["candidates"].is_file() else [],
        "clips": json.loads(clips_manifest.read_text(encoding="utf-8")) if clips_manifest.is_file() else [],
        "picks": json.loads(ps["picks"].read_text(encoding="utf-8")) if ps["picks"].is_file() else [],
        "folder": str(folder),
    }


def summary(store: Store, p: Project) -> dict[str, Any]:
    ps = paths(store, p)
    return {"id": p.id, "name": p.name, "created": p.created, "has_transcript": ps["transcript"].is_file(),
            "clips": (ps["clips"] / "clips.json").is_file()}


def load_transcript(store: Store, p: Project) -> tr.Transcript:
    path = paths(store, p)["transcript"]
    if not path.is_file():
        raise ValueError("This video hasn't been transcribed yet.")
    return tr.load(path)


def save_transcript(store: Store, p: Project, data: dict) -> tr.Transcript:
    t = tr.from_dict(data)  # validates
    ps = paths(store, p)
    tr.save(t, ps["transcript"])
    write_subtitles(t, ps["srt"], ps["vtt"])
    return t


def replace_words(store: Store, p: Project, pairs: list[str]) -> tr.Transcript:
    t = tr.replace_words(load_transcript(store, p), tr.parse_replacements(pairs))
    return save_transcript(store, p, tr.to_dict(t))


def preview_chunks(store: Store, p: Project, style_name: str, emphasis: str = "auto") -> list[dict]:
    """The exact chunks the burn would draw, for the live overlay on the player."""
    t = load_transcript(store, p)
    style = styles.with_emphasis(styles.load_style(style_name), emphasis)
    info = media.probe(Path(p.source))
    rules = fit_rules(style, info.width or 1920, info.height or 1080)
    out = []
    previous = ""
    for c in chunk_words(t.words, rules):
        flags = emphasis_flags(c.words, previous) if style.accent else [False] * len(c.words)
        previous = c.words[-1].text if c.words else previous
        words = [(w, display_text(w.text, style), f) for w, f in zip(c.words, flags)]
        words = [(w, d, f) for w, d, f in words if d]
        if not words:
            continue
        lines = split_lines([d for _, d, _ in words], rules.max_line_chars)
        out.append({"start": c.start, "end": c.end, "break": len(lines[0].split(" ")) if len(lines) == 2 else 0,
                    "accent": style.accent,
                    "words": [{"start": w.start, "end": w.end, "text": d, "emph": f} for w, d, f in words]})
    return out


# --- jobs ---------------------------------------------------------------------

def job_transcribe(store: Store, p: Project, language: str | None, prompt: str | None,
                   replace: list[str]) -> Callable[[Progress], Any]:
    def work(progress: Progress) -> dict:
        from ..transcribe import transcribe

        progress(0.0, "Loading Whisper on the GPU...")
        t = transcribe(Path(p.source), language=language or None, prompt=prompt or None,
                       progress=lambda f: progress(f, "Transcribing"))
        t = tr.replace_words(t, tr.parse_replacements(replace))
        save_transcript(store, p, tr.to_dict(t))
        return {"words": len(t.words), "language": t.language}
    return work


def job_burn(store: Store, p: Project, style_name: str, emphasis: str = "auto") -> Callable[[Progress], Any]:
    style = styles.with_emphasis(styles.load_style(style_name), emphasis)

    def work(progress: Progress) -> dict:
        t = load_transcript(store, p)
        info = media.probe(Path(p.source))
        if info.width == 0:
            raise ValueError("This file has no video track to burn captions onto.")
        from ..ass import to_ass

        out = store.folder(p.id) / f"captioned_{style.name}.mp4"
        media.burn(Path(p.source), to_ass(t.words, style, info.width, info.height), FONTS_DIR, out, info,
                   progress=lambda f: progress(f, f"Burning '{style.name}' captions"))
        return {"file": out.name}
    return work


def job_find(store: Store, p: Project, min_len: float, max_len: float) -> Callable[[Progress], Any]:
    if not 3 <= min_len < max_len <= 180:
        raise ValueError("Clip length must be 3-180 s with min below max.")

    def work(progress: Progress) -> list:
        t = load_transcript(store, p)
        progress(0.1, "Measuring loudness")
        audio = media.load_audio(Path(p.source))
        progress(0.5, "Scoring candidate clips")
        cands = clipfind.find_candidates(t.words, audio, media.SAMPLE_RATE, min_len, max_len)
        if not cands:
            raise ValueError(f"No {min_len:.0f}-{max_len:.0f} s stretch of speech found.")
        ps = paths(store, p)
        ps["candidates"].write_text(clipfind.to_json(cands), encoding="utf-8")
        ps["picks"].unlink(missing_ok=True)  # old picks point at the old candidate ids
        ps["judge"].write_text(clipfind.judge_prompt(cands, p.name, "ranking.json", 5, min_len, max_len),
                               encoding="utf-8")
        return json.loads(clipfind.to_json(cands))
    return work


def judge_prompt(store: Store, p: Project, count: int) -> str:
    cands = _candidates(store, p)
    lens = [c.duration for c in cands]
    return clipfind.judge_prompt(cands, p.name, "ranking.json", count, min(lens), max(lens))


# --- any AI: judge clips, write hook titles, find transcript mistakes ---------------------

def _task(task: str) -> str:
    if task not in ai_tasks.TASKS:
        raise ValueError(f"Unknown AI task {task!r}.")
    return task


def ai_files(store: Store, p: Project, task: str) -> tuple[Path, Path]:
    prompt_name, reply_name = ai_tasks.FILES[_task(task)]
    folder = store.folder(p.id)
    return folder / prompt_name, folder / reply_name


def ai_prompt(store: Store, p: Project, task: str, count: int = 5, clips: list | None = None) -> dict:
    """The prompt for a task, also saved in the project folder so an AI agent can read it."""
    if _task(task) == "judge":
        prompt = judge_prompt(store, p, count)
    elif task == "titles":
        prompt = ai_tasks.titles_prompt(p.name, ai_tasks.clip_texts(load_transcript(store, p).words, clips or []))
    else:
        prompt = ai_tasks.fixes_prompt(p.name, load_transcript(store, p).words)
    prompt_path, reply_path = ai_files(store, p, task)
    prompt_path.write_text(prompt, encoding="utf-8")
    return {"prompt": prompt, "prompt_path": str(prompt_path), "reply_path": str(reply_path)}


def ai_apply(store: Store, p: Project, task: str, entries: object, count: int = 5,
             clips: list | None = None) -> list:
    """Validate an AI's reply. judge -> picked clips (saved); titles -> [{id, title}];
    fixes -> suggestions [{find, replace, why, count}] that the user applies one by one."""
    if _task(task) == "judge":
        if not isinstance(entries, list):
            raise ValueError("The reply must be a JSON array.")
        return apply_selection(store, p, entries, count)
    words = load_transcript(store, p).words
    if task == "titles":
        return ai_tasks.parse_titles(entries, {c.id for c in ai_tasks.clip_texts(words, clips or [])})
    return ai_tasks.parse_fixes(entries, words)


def ai_reply_from_file(store: Store, p: Project, task: str) -> list:
    path = ai_files(store, p, task)[1]
    if not path.is_file():
        raise FileNotFoundError(f"No {path.name} yet in {path.parent}.")
    return clipfind.load_ranking(path)


def job_ai(store: Store, p: Project, task: str, url: str, model: str, key: str | None, count: int,
           clips: list | None) -> Callable[[Progress], Any]:
    judge_llm.check_url(url)
    if not model.strip():
        raise ValueError("Name the model to use, e.g. qwen3:8b")
    prompt = ai_prompt(store, p, task, count, clips)["prompt"]
    verb = {"judge": "pick the best clips", "titles": "write hook titles", "fixes": "check the transcript"}[task]

    def work(progress: Progress) -> list:
        progress(0.1, f"Asking {model} to {verb}")
        return ai_apply(store, p, task, _ask_cancellable(progress, prompt, url, model, key), count, clips)
    return work


def _ask_cancellable(progress: Progress, prompt: str, url: str, model: str, key: str | None = None) -> list:
    """Ask the AI on a side thread so Cancel works while it thinks; an abandoned request just times out."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["reply"] = judge_llm.ask(prompt, url, model, key=key)
        except Exception as e:  # handed back to the job below
            box["error"] = e
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.5)
        progress(0.1)  # raises Cancelled once the user presses Cancel
    if "error" in box:
        raise box["error"]
    return box["reply"]


def job_judge_llm(store: Store, p: Project, url: str, model: str, count: int) -> Callable[[Progress], Any]:
    judge_llm.check_url(url)
    if not model.strip():
        raise ValueError("Name the model to use, e.g. qwen3:8b")
    prompt = judge_prompt(store, p, count)

    def work(progress: Progress) -> list:
        progress(0.1, f"Asking {model} to judge the clips")
        return apply_selection(store, p, _ask_cancellable(progress, prompt, url, model), count)
    return work


def apply_selection(store: Store, p: Project, entries: list, count: int = 50) -> list[dict]:
    """A judge's ranking or the user's picks -> validated, snapped clips (not rendered yet)."""
    chosen = clipfind.apply_ranking(_candidates(store, p), entries, load_transcript(store, p).words, count)
    text = clipfind.to_json(chosen)
    paths(store, p)["picks"].write_text(text, encoding="utf-8")  # survives a page reload
    return json.loads(text)


def job_render(store: Store, p: Project, entries: list, style_name: str, layout: str,
               emphasis: str = "auto", title_look: str = "banner",
               title_secs: float = 3.5) -> Callable[[Progress], Any]:
    styles.with_emphasis(styles.load_style(style_name), emphasis)  # validates both
    if layout not in ("auto", "crop", "fit"):
        raise ValueError("layout must be auto, crop or fit")
    if title_look not in titles.LOOKS:
        raise ValueError(f"title look must be one of {', '.join(titles.LOOKS)}")
    if not 0 <= title_secs <= 60:
        raise ValueError("title seconds must be 0-60 (0 = the whole clip)")
    chosen = clipfind.apply_ranking(_candidates(store, p), entries, load_transcript(store, p).words, 50)

    def work(progress: Progress) -> list:
        words = load_transcript(store, p).words
        return render_clips(Path(p.source), chosen, words, paths(store, p)["clips"], style_name, layout,
                            progress=progress, title_look=title_look, title_secs=title_secs,
                            emphasis=emphasis)
    return work


def _candidates(store: Store, p: Project) -> tuple[clipfind.Candidate, ...]:
    path = paths(store, p)["candidates"]
    if not path.is_file():
        raise ValueError("Find clips first.")
    return clipfind.load_candidates(path)


# --- graphics for the UI: waveform and thumbnail strips ---------------------------

WAVE_BUCKETS = 1200
_thumb_slots = threading.BoundedSemaphore(2)  # a few GPU decodes at once, never a flood


def waveform(store: Store, p: Project) -> list[float]:
    """Peak loudness per bucket across the whole video (0-1), cached next to the project."""
    path = paths(store, p)["waveform"]
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # a broken cache is rebuilt below
    audio = media.load_audio(Path(p.source))
    if not len(audio):
        return []
    edges = np.linspace(0, len(audio), WAVE_BUCKETS + 1).astype(int)
    peaks = np.array([np.abs(audio[a:b]).max() if b > a else 0.0 for a, b in zip(edges, edges[1:])])
    peaks = np.sqrt(peaks / (peaks.max() or 1.0))  # sqrt lifts quiet speech so the shape reads
    out = [round(float(x), 3) for x in peaks]
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.partial")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, path)
    return out


def strip(store: Store, p: Project, times: list[float], height: int = 120) -> Path:
    """A JPEG of frames at `times`, side by side, decoded on the GPU (NVDEC). Cached."""
    info = media.probe(Path(p.source))
    if info.width == 0:
        raise ValueError("This file has no video track.")
    times = [round(min(max(0.0, t), max(0.0, info.duration - 0.1)), 2) for t in times[:6]]
    if not times:
        raise ValueError("No frame times given.")
    folder = paths(store, p)["thumbs"]
    folder.mkdir(exist_ok=True)
    key = hashlib.sha1(f"{times}-{height}".encode()).hexdigest()[:16]
    out = folder / f"strip_{key}.jpg"
    if out.is_file():
        return out
    width = round(height * info.width / max(1, info.height) / 2) * 2
    tmp = folder / f"{out.stem}.{uuid.uuid4().hex[:8]}.partial.jpg"  # unique: two tabs may ask at once
    args = ["ffmpeg", "-v", "error", "-y"]
    for t in times:
        args += ["-hwaccel", "cuda", "-ss", f"{t:.2f}", "-i", str(Path(p.source).resolve())]
    scaled = "".join(f"[{i}:v]scale={width}:{height},setsar=1[s{i}];" for i in range(len(times)))
    joined = "".join(f"[s{i}]" for i in range(len(times)))
    graph = scaled + (f"{joined}hstack=inputs={len(times)}[v]" if len(times) > 1 else "[s0]null[v]")
    args += ["-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-q:v", "4",
             str(tmp)]
    with _thumb_slots:
        done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"Couldn't grab frames: {done.stderr.strip()[-200:]}")
    os.replace(tmp, out)
    return out
