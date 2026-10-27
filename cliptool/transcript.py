"""Transcript data model: immutable words and segments, saved as editable JSON."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT_VERSION = 1


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Transcript:
    language: str
    duration: float
    model: str
    segments: tuple[Segment, ...]

    @property
    def words(self) -> tuple[Word, ...]:
        return tuple(w for s in self.segments for w in s.words)


def to_dict(t: Transcript) -> dict:
    return {
        "format": FORMAT_VERSION,
        "language": t.language,
        "duration": t.duration,
        "model": t.model,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [asdict(w) for w in s.words],
            }
            for s in t.segments
        ],
    }


def from_dict(d: dict) -> Transcript:
    if d.get("format") != FORMAT_VERSION:
        raise ValueError(f"Unsupported transcript format: {d.get('format')!r}")
    segments = []
    for s in d["segments"]:
        words = tuple(
            Word(float(w["start"]), float(w["end"]), str(w["text"]), float(w.get("prob", 1.0)))
            for w in s.get("words", [])
        )
        for w in words:
            if w.end < w.start:
                raise ValueError(f"Word {w.text!r} ends before it starts ({w.start} > {w.end})")
        segments.append(Segment(float(s["start"]), float(s["end"]), str(s["text"]), words))
    return Transcript(str(d["language"]), float(d["duration"]), str(d["model"]), tuple(segments))


def parse_replacements(pairs: list[str] | None) -> dict[str, str]:
    """["Callan=Callum", ...] -> {"Callan": "Callum"}"""
    out = {}
    for pair in pairs or []:
        old, sep, new = pair.partition("=")
        if not sep or not old.strip() or not new.strip():
            raise ValueError(f"--replace needs OLD=NEW, got {pair!r}")
        out[old.strip()] = new.strip()
    return out


def replace_words(t: Transcript, mapping: dict[str, str]) -> Transcript:
    """Swap whole words (case-sensitive), keeping the punctuation around them: Callan's -> Callum's."""
    if not mapping:
        return t
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")\b")

    def fix(text: str) -> str:
        return pattern.sub(lambda m: mapping[m.group(1)], text)

    segments = tuple(
        Segment(s.start, s.end, fix(s.text), tuple(Word(w.start, w.end, fix(w.text), w.prob) for w in s.words))
        for s in t.segments
    )
    return Transcript(t.language, t.duration, t.model, segments)


def save(t: Transcript, path: Path) -> None:
    path.write_text(json.dumps(to_dict(t), ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: Path) -> Transcript:
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8-sig")))
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"{path} is not a valid transcript file: {e}") from e
