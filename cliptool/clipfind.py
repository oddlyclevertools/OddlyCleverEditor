"""Find standalone short clips in a long transcript.

Candidates are runs of whole sentences. A heuristic scores them (hook, clean cut points,
pace, loudness, length). An external judge (any LLM, e.g. Claude Code or a local model) can
re-rank them through a prompt file and a JSON ranking file.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from .chunking import STRONG_PUNCT, clean_words
from .transcript import Word

log = logging.getLogger("cliptool")

DANGLING_STARTS = frozenset(
    "and but so because which then also or plus nor yet he she it they him her them his its their "
    "theirs that's it's he's she's they're".split()
)
STRONG_WORDS = frozenset(
    "never always nobody everyone everything nothing secret worst best only first last most least "
    "dead died die death kill killed war plague fire money rich poor free million billion thousand "
    "why how what impossible mistake truth lie lied crazy insane weird actually".split()
)
NUMBER_WORDS = frozenset("one two three four five six seven eight nine ten hundred thousand million".split())


@dataclass(frozen=True)
class Sentence:
    start: float
    end: float
    text: str
    n_words: int


@dataclass(frozen=True)
class Candidate:
    id: int
    start: float
    end: float
    score: float
    parts: dict
    sentences: tuple[Sentence, ...]
    title: str = ""
    reason: str = ""
    source: str = "heuristic"

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)


def sentences_from_words(words: tuple[Word, ...] | list[Word], max_gap: float = 1.5) -> tuple[Sentence, ...]:
    """Split at sentence punctuation (or a long silence, for unpunctuated speech)."""
    out, current = [], []
    clean = clean_words(words)
    for i, w in enumerate(clean):
        current.append(w)
        nxt = clean[i + 1] if i + 1 < len(clean) else None
        if nxt is None or w.text.endswith(STRONG_PUNCT) or nxt.start - w.end > max_gap:
            out.append(Sentence(current[0].start, current[-1].end, " ".join(x.text for x in current), len(current)))
            current = []
    return tuple(out)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def hook_score(first: Sentence) -> float:
    toks = _tokens(first.text)
    if not toks:
        return 0.0
    score = 0.2
    if first.text.endswith("?"):
        score += 0.3
    if any(t.isdigit() or t in NUMBER_WORDS for t in toks):
        score += 0.15
    if "you" in toks or "your" in toks:
        score += 0.15
    if any(t in STRONG_WORDS for t in toks):
        score += 0.25
    if first.n_words <= 12:
        score += 0.15
    if toks[0] in DANGLING_STARTS:
        score -= 0.6  # starts mid-thought: makes no sense without the previous line
    return float(np.clip(score, 0.0, 1.0))


def loudness_curve(audio: np.ndarray, sample_rate: int, step: float = 0.5) -> np.ndarray:
    """RMS per `step` seconds, normalised so the video's median is 1."""
    n = int(sample_rate * step)
    if n <= 0 or audio.size < n:
        return np.ones(1)
    frames = audio[: audio.size // n * n].reshape(-1, n)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    med = float(np.median(rms)) or 1.0
    return rms / med


def score_window(sents: tuple[Sentence, ...], i: int, j: int, loud: np.ndarray, step: float,
                 target_len: float) -> tuple[float, dict]:
    first, last = sents[i], sents[j]
    start, end = first.start, last.end
    dur = end - start
    gap_before = first.start - sents[i - 1].end if i > 0 else 2.0
    gap_after = sents[j + 1].start - last.end if j + 1 < len(sents) else 2.0
    words = sum(s.n_words for s in sents[i : j + 1])
    wps = words / dur if dur > 0 else 0.0
    a, b = int(start / step), max(int(end / step), int(start / step) + 1)
    seg = loud[a:b] if a < len(loud) else np.ones(1)
    ending = 0.4 + (0.3 if last.n_words <= 10 else 0.0) + (0.3 if last.text.endswith(("!", "?")) else 0.0)
    parts = {
        "hook": hook_score(first),
        "boundary": float(np.clip((min(gap_before, 1.0) + min(gap_after, 1.0)) / 2, 0, 1)),
        "ending": float(min(ending, 1.0)),
        "pace": float(np.clip(1 - abs(wps - 2.8) / 1.5, 0, 1)),
        "energy": float(np.clip((seg.mean() - 0.7) / 0.6, 0, 1)) if seg.size else 0.0,
        "length": float(np.clip(1 - abs(dur - target_len) / target_len, 0, 1)),
    }
    weights = {"hook": 0.35, "boundary": 0.2, "ending": 0.15, "pace": 0.1, "energy": 0.1, "length": 0.1}
    total = sum(parts[k] * w for k, w in weights.items())
    return round(total, 4), {k: round(v, 3) for k, v in parts.items()}


def find_candidates(words: tuple[Word, ...], audio: np.ndarray | None, sample_rate: int,
                    min_len: float = 20.0, max_len: float = 60.0, limit: int = 25) -> tuple[Candidate, ...]:
    """Best non-overlapping windows of whole sentences, highest score first."""
    sents = sentences_from_words(words)
    step = 0.5
    loud = loudness_curve(audio, sample_rate, step) if audio is not None else np.ones(1)
    target = (min_len + max_len) / 2
    scored = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j].end - sents[i].start
            if dur > max_len:
                break
            if dur >= min_len:
                score, parts = score_window(sents, i, j, loud, step, target)
                scored.append((score, i, j, parts))
    scored.sort(key=lambda x: -x[0])
    chosen: list[tuple[float, int, int, dict]] = []
    for cand in scored:
        _, i, j, _ = cand
        if all(j < ci or i > cj for _, ci, cj, _ in chosen):
            chosen.append(cand)
            if len(chosen) == limit:
                break
    return tuple(
        Candidate(n, sents[i].start, sents[j].end, score, parts, sents[i : j + 1])
        for n, (score, i, j, parts) in enumerate(chosen, 1)
    )


JUDGE_INSTRUCTIONS = """# Pick the best short clips

You are an experienced short-form video editor. Below are candidate clips cut from a long video
("{video}"), each a run of whole sentences with timestamps in seconds. Pick the {count} that would
work best as standalone YouTube Shorts / Reels / TikToks.

Judge each on:
1. **Hook**: do the first 3 seconds make a viewer stop scrolling?
2. **Stands alone**: does it make sense with no context from the rest of the video?
3. **Payoff**: does it end on a punchline, reveal or satisfying line (not mid-thought)?
4. **Shareable**: surprising, funny or useful enough to send to a friend?

You may tighten a clip by giving a new `start` and/or `end`, but only at sentence boundaries listed
below. Keep each clip between {min_len:.0f} and {max_len:.0f} seconds.

Reply with **only** a JSON array, best first, saved as `{ranking}`:

```json
[{{"id": 7, "score": 9, "title": "Short punchy title", "reason": "why it works", "start": 431.2, "end": 478.9}}]
```

`start`/`end` are optional. `score` is 1-10.

## Candidates
"""


def judge_prompt(cands: tuple[Candidate, ...], video: str, ranking_name: str, count: int,
                 min_len: float, max_len: float) -> str:
    out = [JUDGE_INSTRUCTIONS.format(video=video, count=count, ranking=ranking_name,
                                     min_len=min_len, max_len=max_len)]
    for c in cands:
        out.append(f"\n### Candidate {c.id}: {c.start:.1f}-{c.end:.1f} s ({c.duration:.0f} s), heuristic {c.score:.2f}\n")
        out.extend(f"- [{s.start:.1f}-{s.end:.1f}] {s.text}" for s in c.sentences)
    return "\n".join(out) + "\n"


def _num(entry: dict, key: str, default: float) -> float:
    value = entry.get(key)
    return default if value is None else float(value)


def _text(entry: dict, key: str) -> str:
    value = entry.get(key)
    return "" if value is None else str(value)


def _snap(t: float, points: list[float]) -> float:
    return min(points, key=lambda p: abs(p - t))


def apply_ranking(cands: tuple[Candidate, ...], ranking: list, words: tuple[Word, ...],
                  count: int) -> tuple[Candidate, ...]:
    """Validate a judge's JSON ranking and turn it into clips (times snapped to sentences)."""
    if not isinstance(ranking, list):
        raise ValueError("Ranking must be a JSON array of objects with an 'id'.")
    by_id = {c.id: c for c in cands}
    sents = sentences_from_words(words)
    starts, ends = [s.start for s in sents], [s.end for s in sents]
    out: list[Candidate] = []
    for entry in ranking:
        cid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(cid, int) or isinstance(cid, bool) or cid not in by_id:
            raise ValueError(f"Ranking entry {entry!r} doesn't name a candidate id.")
        base = by_id[cid]
        try:
            start = _snap(_num(entry, "start", base.start), starts)
            end = _snap(_num(entry, "end", base.end), ends)
            score = _num(entry, "score", base.score)
        except (TypeError, ValueError):
            log.warning("Skipping ranking entry %s: start/end/score must be numbers.", cid)
            continue
        if not 3.0 <= end - start <= 180.0:
            log.warning("Skipping clip %s: %.1f-%.1f s is not 3-180 s long.", cid, start, end)
            continue
        if any(not (end <= c.start or start >= c.end) for c in out):
            continue  # overlaps a clip the judge ranked higher
        inside = tuple(s for s in sents if s.start >= start - 0.01 and s.end <= end + 0.01)
        out.append(replace(base, start=start, end=end, sentences=inside, source="judge", score=score,
                           title=_text(entry, "title"), reason=_text(entry, "reason")))
        if len(out) == count:
            break
    if not out:
        raise ValueError("The ranking selected no usable clips.")
    return tuple(out)


def to_json(cands: tuple[Candidate, ...]) -> str:
    rows = []
    for c in cands:
        row = asdict(c)
        row["sentences"] = [asdict(s) for s in c.sentences]
        row["duration"] = round(c.duration, 3)
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=1)


def load_candidates(path: Path) -> tuple[Candidate, ...]:
    """Read back candidates.json, so a judge's ids always mean the windows it was shown."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        return tuple(
            Candidate(int(r["id"]), float(r["start"]), float(r["end"]), float(r["score"]), dict(r["parts"]),
                      tuple(Sentence(float(s["start"]), float(s["end"]), str(s["text"]), int(s["n_words"]))
                            for s in r["sentences"]))
            for r in rows
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{path} is not a valid candidates file: {e}") from e


def pad_bounds(start: float, end: float, words: tuple[Word, ...], duration: float,
               lead: float = 0.15, tail: float = 0.25) -> tuple[float, float]:
    """Breathe a little around the cut (Whisper word starts run late) without catching
    the neighbouring words."""
    before = max((w.end for w in words if w.end <= start + 0.01), default=0.0)
    after = min((w.start for w in words if w.start >= end - 0.01), default=end + 2 * tail)
    new_start = max(0.0, start - min(lead, max(0.0, start - before) / 2))
    new_end = end + min(tail, max(0.0, after - end) / 2)
    if duration:
        new_end = min(duration, new_end)
    return round(new_start, 3), round(max(new_end, end), 3)


def load_ranking(path: Path) -> list:
    return load_ranking_text(path.read_text(encoding="utf-8-sig"), str(path))


def load_ranking_text(text: str, source: str = "The ranking") -> list:
    """Parse a judge's reply: bare JSON, or a chat answer with a ```json block in it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if not fenced:
        bracket = re.search(r"\[.*\]", text, re.S)  # JSON array inside surrounding prose
        candidate = bracket.group(0) if bracket else text
    else:
        candidate = fenced.group(1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"{source} isn't valid JSON: {e}") from e


def slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]
    return s or fallback
