"""Plain subtitle files: SRT and WebVTT."""
from __future__ import annotations

from .chunking import Chunk, split_lines


def _stamp(seconds: float, sep: str) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _lines(chunk: Chunk, max_line_chars: int) -> str:
    return "\n".join(split_lines([w.text for w in chunk.words], max_line_chars))


def to_srt(chunks: tuple[Chunk, ...], max_line_chars: int = 42) -> str:
    blocks = [
        f"{i}\n{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}\n{_lines(c, max_line_chars)}\n"
        for i, c in enumerate(chunks, 1)
    ]
    return "\n".join(blocks)


def to_vtt(chunks: tuple[Chunk, ...], max_line_chars: int = 42) -> str:
    blocks = [
        f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}\n{_lines(c, max_line_chars)}\n"
        for c in chunks
    ]
    return "WEBVTT\n\n" + "\n".join(blocks)
