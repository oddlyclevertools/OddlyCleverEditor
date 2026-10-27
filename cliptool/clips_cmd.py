"""`cliptool clips`: find the best moments, optionally let an LLM judge them, export 9:16 Shorts."""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Callable

from . import clipfind, export, judge_llm, media, reframe, styles
from . import transcript as tr

log = logging.getLogger("cliptool")


def _quiet_opencv() -> None:
    try:
        import cv2

        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except (ImportError, AttributeError):
        pass


def run(args: argparse.Namespace) -> None:
    from . import cli  # late import: cli imports this module

    if not 3 <= args.min_len < args.max_len <= 180:
        raise ValueError("Need 3 <= --min < --max <= 180 seconds.")
    if args.count < 1:
        raise ValueError("--count must be at least 1.")
    t = tr.load(cli.ensure_transcript(args))
    folder = args.out_dir or args.video.parent
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / args.video.stem

    cand_path = Path(f"{stem}.candidates.json")
    if args.ranking:
        # Use the exact candidates the judge was shown; rebuilding them could renumber the ids.
        if not cand_path.is_file():
            raise RuntimeError(f"{cand_path.name} not found next to the outputs. Run with --judge first.")
        cands = clipfind.load_candidates(cand_path)
        chosen = clipfind.apply_ranking(cands, clipfind.load_ranking(args.ranking), t.words, args.count)
    else:
        cands = clipfind.find_candidates(t.words, media.load_audio(args.video), media.SAMPLE_RATE,
                                         args.min_len, args.max_len)
        if not cands:
            raise RuntimeError(f"No {args.min_len:.0f}-{args.max_len:.0f} s stretch of speech found.")
        cand_path.write_text(clipfind.to_json(cands), encoding="utf-8")
        log.info("Found %d candidate clips -> %s", len(cands), cand_path.name)
        if args.judge:
            write_judge_prompt(args, cands, stem)
            return
        if args.judge_url:
            if not judge_llm.is_local(args.judge_url):
                log.warning("Note: %s isn't on this PC, so the transcript text is sent there.", args.judge_url)
            log.info("Asking %s (%s) to judge %d candidates...", args.judge_url, args.judge_model, len(cands))
            prompt = clipfind.judge_prompt(cands, args.video.name, "ranking.json", args.count,
                                           args.min_len, args.max_len)
            chosen = clipfind.apply_ranking(cands, judge_llm.ask(prompt, args.judge_url, args.judge_model),
                                            t.words, args.count)
        else:
            chosen = cands[: args.count]

    for n, c in enumerate(chosen, 1):
        log.info("%d. %s  %s-%s (%.0f s)  score %.2f%s", n, c.title or c.sentences[0].text[:60],
                 _mmss(c.start), _mmss(c.end), c.duration, c.score, f" [{c.source}]")
    if args.no_render:
        return
    render_clips(args.video, chosen, t.words, Path(f"{stem}_clips"), args.style, args.layout,
                 args.encoder, args.quality, title_look=args.title_look, title_secs=args.title_secs,
                 emphasis=args.emphasis)


def write_judge_prompt(args: argparse.Namespace, cands: tuple[clipfind.Candidate, ...], stem: Path) -> None:
    ranking_path = Path(f"{stem}.ranking.json")
    prompt_path = Path(f"{stem}.judge.md")
    prompt_path.write_text(clipfind.judge_prompt(cands, args.video.name, ranking_path.name, args.count,
                                                 args.min_len, args.max_len), encoding="utf-8")
    again = f'oddlyclever clips "{args.video}" --ranking "{ranking_path}"'
    for flag, value, default in (("--out-dir", args.out_dir, None), ("--transcript", args.transcript, None),
                                 ("--count", args.count, 5), ("--layout", args.layout, "auto"),
                                 ("--style", args.style, "bold")):
        if value != default:
            again += f' {flag} "{value}"'
    log.info("Judge prompt -> %s\nGive it to any LLM (e.g. ask Claude Code to follow it), save the JSON "
             "reply as %s, then run:\n  %s", prompt_path, ranking_path.name, again)


def render_clips(video: Path, chosen: tuple[clipfind.Candidate, ...], words: tuple[tr.Word, ...],
                 out_dir: Path, style_name: str = "bold", layout_mode: str = "auto",
                 encoder: str = "h264_nvenc", quality: int = 19,
                 progress: Callable[[float, str], None] | None = None, title_look: str = "banner",
                 title_secs: float = 3.5, emphasis: str = "auto") -> list[dict]:
    """Reframe, caption and encode each clip. Returns the manifest (also saved as clips.json)."""
    from .cli import FONTS_DIR

    _quiet_opencv()
    info = media.probe(video)
    if info.width == 0:
        raise RuntimeError(f"{video} has no video track.")
    style = styles.with_emphasis(styles.load_style(style_name), emphasis)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    total = len(chosen)
    for n, c in enumerate(chosen, 1):
        start, end = clipfind.pad_bounds(c.start, c.end, words, info.duration)
        name = f"{n:02d}_{clipfind.slug(c.title or c.sentences[0].text, f'clip_{c.id}')}.mp4"
        started = time.time()
        track = None
        if progress:
            progress((n - 1) / total, f"Clip {n}/{total}: finding faces")
        if layout_mode in ("auto", "crop") and info.width > info.height:
            track = reframe.analyse(video, start, end, info.width, info.height, info.fps)
            if progress:
                progress((n - 1 + 0.2) / total, f"Clip {n}/{total}: rendering")

        def clip_progress(f: float, n: int = n) -> None:
            if progress:
                progress((n - 1 + 0.2 + 0.8 * f) / total, f"Clip {n}/{total}: rendering")

        layout = export.render(video, info, start, end, words, style, layout_mode, track,
                               FONTS_DIR, out_dir / name, encoder, quality, clip_progress,
                               c.title, title_look, title_secs)
        extra = f", faces tracked {track.coverage:.0%} over {track.shots} shots" if track else ""
        log.info("  %s  %s layout%s, %.1f s", name, layout, extra, time.time() - started)
        manifest.append({"file": name, "start": start, "end": end, "duration": round(end - start, 3),
                         "title": c.title, "reason": c.reason, "score": c.score, "source": c.source,
                         "layout": layout, "text": c.text, "style": style.name, "title_look": title_look})
    save_manifest(out_dir, manifest)
    log.info("Wrote %d clips to %s", len(manifest), out_dir)
    return manifest


def save_manifest(out_dir: Path, new: list[dict]) -> list[dict]:
    """clips.json lists the newest render first, then earlier clips whose files still exist."""
    path = out_dir / "clips.json"
    names = {m["file"] for m in new}
    try:
        earlier = [m for m in json.loads(path.read_text(encoding="utf-8"))
                   if isinstance(m, dict) and m.get("file") not in names and (out_dir / str(m.get("file"))).is_file()]
    except (OSError, ValueError, TypeError):
        earlier = []
    merged = new + earlier
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return merged


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"
