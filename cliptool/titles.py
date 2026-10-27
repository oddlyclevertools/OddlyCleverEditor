"""Animated hook titles for the top of a Short (extra ASS styles and events)."""
from __future__ import annotations

from .ass import _escape, _ts
from .chunking import split_lines
from .styles import ass_color

LOOKS = ("none", "banner", "sticker", "outline")
_MAX_CHARS = 60


def _lines(title: str, max_line: int) -> str:
    words = _escape(title).upper().split()
    if not words:
        return ""
    text = " ".join(words)
    if len(text) > _MAX_CHARS:  # keep a hook short: cut at a word boundary
        text = text[:_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        words = text.split()
    lines = split_lines(words, max_line)
    return "\\N".join(lines)


def _style(name: str, font: str, px: int, primary: str, outline_color: str, border_style: int,
           outline: float, shadow: float, back: str = "#00000090") -> str:
    fields = [name, font, px, ass_color(primary), ass_color(primary), ass_color(outline_color), ass_color(back),
              0, 0, 0, 0, 100, 100, 0, 0, border_style, f"{outline:.1f}", f"{shadow:.1f}", 8, 60, 60, 0, 1]
    return f"Style: {','.join(str(f) for f in fields)}\n"


def title_ass(title: str, look: str, width: int, height: int, duration: float,
              seconds: float = 3.5, accent: str = "#FFE14D") -> tuple[str, list[str]]:
    """Returns (style lines, dialogue events). seconds <= 0 keeps the title for the whole clip."""
    if look not in LOOKS:
        raise ValueError(f"title look must be one of {', '.join(LOOKS)}")
    end = duration if seconds <= 0 else min(duration, seconds)
    px = round(width * 0.084)
    longest = max((len(w) for w in title.split()), default=1)
    px = min(px, int(width * 0.86 / (max(1, min(longest, _MAX_CHARS)) * 0.7)))  # one long word still fits
    text = _lines(title, max(8, int(width * 0.8 / (px * 0.7))))
    if look == "none" or not text or end <= 0.2:
        return "", []
    x, y = width // 2, round(height * 0.11)
    fade_out = min(250, int(end * 1000) // 4)
    if look == "banner":
        # A yellow bar slides in from the left, the text follows a beat later.
        styles = _style("TitleBox", "Montserrat Black", px, "#111111", accent, 3, px * 0.32, px * 0.08)
        tags = (f"{{\\move({-width // 2},{y},{x},{y},0,260)\\fscx120\\t(0,260,\\fscx100)"
                f"\\fad(0,{fade_out})}}")
        return styles, [f"Dialogue: 5,{_ts(0)},{_ts(end)},TitleBox,,0,0,0,,{tags}{text}"]
    if look == "sticker":
        # A white tilted card that pops with overshoot.
        styles = _style("TitleBox", "Poppins Black", px, "#111111", "#FFFFFF", 3, px * 0.34, px * 0.12)
        tags = (f"{{\\pos({x},{y})\\frz-3\\fscx20\\fscy20\\t(0,140,\\fscx112\\fscy112)"
                f"\\t(140,240,\\fscx100\\fscy100)\\fad(0,{fade_out})}}")
        return styles, [f"Dialogue: 5,{_ts(0)},{_ts(end)},TitleBox,,0,0,0,,{tags}{text}"]
    # outline: big outlined type that rises and fades in; the last line in the accent colour.
    styles = _style("Title", "Archivo Black", round(px * 1.1), "#FFFFFF", "#000000", 1, px * 0.14, px * 0.06)
    parts = text.split("\\N")
    if len(parts) > 1:
        parts[-1] = f"{{\\c{ass_color(accent)}}}{parts[-1]}"
    tags = f"{{\\move({x},{y + 50},{x},{y},0,300)\\fad(220,{fade_out})}}"
    joined = "\\N".join(parts)
    return styles, [f"Dialogue: 5,{_ts(0)},{_ts(end)},Title,,0,0,0,,{tags}{joined}"]
