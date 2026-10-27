"""Group timed words into caption chunks (what is on screen at once)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .transcript import Word

STRONG_PUNCT = (".", "?", "!", "…")
SOFT_PUNCT = (",", ";", ":", "—", "–")
GLUE = re.compile(r"^[,.\-%'’][\w%]")  # a token that continues the previous word
GLUE_MAX_GAP = 0.25
WEAK_ENDINGS = frozenset(
    "a an the and or but of to in on at for with from by as is was that this my his her their our your".split()
)


@dataclass(frozen=True)
class ChunkRules:
    max_words: int = 7
    max_chars: int = 84
    max_duration: float = 6.0
    max_gap: float = 0.7  # a pause longer than this starts a new chunk
    soft_break_min_words: int = 3  # break after a comma once the chunk has this many words
    min_duration: float = 0.8  # stretch short chunks into the following silence
    max_line_chars: int = 42


@dataclass(frozen=True)
class Chunk:
    start: float
    end: float
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def clean_words(words: tuple[Word, ...] | list[Word]) -> list[Word]:
    """Strip blanks and glue split tokens (Whisper's "2" + ",000", "Anglo" + "-Saxons")."""
    out: list[Word] = []
    for w in words:
        text = w.text.strip()
        if not text:
            continue
        if out and GLUE.match(text) and w.start - out[-1].end < GLUE_MAX_GAP:
            prev = out[-1]
            out[-1] = Word(prev.start, max(w.end, prev.end), prev.text + text, min(prev.prob, w.prob))
        else:
            out.append(Word(w.start, max(w.end, w.start), text, w.prob))
    return out


def _close_reason(current: list[Word], nxt: Word | None, rules: ChunkRules) -> str | None:
    """'natural' at sentence ends and pauses, 'limit' when the chunk is full, else None."""
    last = current[-1]
    if nxt is None or last.text.endswith(STRONG_PUNCT) or nxt.start - last.end > rules.max_gap:
        return "natural"
    if last.text.endswith(SOFT_PUNCT) and len(current) >= rules.soft_break_min_words:
        return "natural"
    chars = sum(len(w.text) + 1 for w in current) + len(nxt.text)
    if (len(current) >= rules.max_words or chars > rules.max_chars
            or nxt.end - current[0].start > rules.max_duration):
        return "limit"
    return None


def chunk_words(words: tuple[Word, ...] | list[Word], rules: ChunkRules) -> tuple[Chunk, ...]:
    """Split words into chunks, then fix timing so chunks never overlap."""
    clean = clean_words(words)
    groups: list[list[Word]] = []
    current: list[Word] = []
    for i, w in enumerate(clean):
        current.append(w)
        nxt = clean[i + 1] if i + 1 < len(clean) else None
        reason = _close_reason(current, nxt, rules)
        if reason is None:
            continue
        carry: list[Word] = []
        # Don't strand "and", "the", "of"... at the end of a line cut by length.
        while reason == "limit" and len(current) > 2 and current[-1].text.lower() in WEAK_ENDINGS:
            carry.insert(0, current.pop())
        groups.append(current)
        current = carry

    chunks = []
    for i, g in enumerate(groups):
        start = g[0].start
        end = max(g[-1].end, start + rules.min_duration)
        if i + 1 < len(groups):
            # Never overlap the next chunk, even if Whisper's word times do.
            end = max(min(end, groups[i + 1][0].start), start + 0.05)
        chunks.append(Chunk(start, end, tuple(g)))
    return tuple(chunks)


def split_lines(words: tuple[str, ...] | list[str], max_line_chars: int) -> tuple[str, ...]:
    """One line if it fits, else two lines broken at the most balanced word gap."""
    line = " ".join(words)
    if len(line) <= max_line_chars or len(words) < 2:
        return (line,)
    best, best_score = 1, None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        score = max(len(a), len(b))
        if best_score is None or score < best_score:
            best, best_score = k, score
    return (" ".join(words[:best]), " ".join(words[best:]))
