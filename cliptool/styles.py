"""Caption style presets (JSON files in presets/, or any JSON path)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .chunking import ChunkRules

PRESETS_DIR = Path(__file__).resolve().parent / "presets"
POSITIONS = {"bottom": 2, "center": 5, "top": 8}
EFFECTS = ("highlight", "karaoke", "boxed", "bounce", "typewriter")
_HEX = re.compile(r"^#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?$")


@dataclass(frozen=True)
class Style:
    name: str
    font: str
    size: float  # fraction of the video's shorter side
    uppercase: bool
    strip_punct: bool
    color: str
    highlight: str | None
    highlight_scale: float
    outline_color: str
    outline: float  # fraction of font size
    shadow: float  # fraction of font size
    shadow_color: str
    box: bool
    box_color: str
    box_padding: float  # fraction of font size
    position: str
    margin_v: float  # fraction of video height
    pop_in: bool
    chunk: ChunkRules
    effect: str = "highlight"  # see EFFECTS
    reveal: bool = False  # words appear only once spoken
    tilt: float = 0.0  # degrees, whole caption
    glow: float = 0.0  # fraction of font size; 0 = off
    glow_color: str = "#00E5FF"
    accent: str | None = None  # colour for numbers and names (emphasis); None = off
    active_color: str | None = None  # text colour of the spoken word on its box (boxed)
    char_width: float | None = None  # average glyph width / font size, for line fitting


def ass_color(hex_rgba: str) -> str:
    """'#RRGGBB' or '#RRGGBBAA' (AA = opacity) -> ASS '&HAABBGGRR' (AA = transparency)."""
    m = _HEX.match(hex_rgba)
    if not m:
        raise ValueError(f"Bad colour {hex_rgba!r}; use #RRGGBB or #RRGGBBAA")
    rgb, alpha = m.group(1), m.group(2) or "FF"
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{255 - int(alpha, 16):02X}{b}{g}{r}".upper()


EMPHASIS = ("auto", "on", "off")
DEFAULT_ACCENT = "#FFB020"


def with_emphasis(style: Style, mode: str) -> Style:
    """auto: the preset's own choice; on: colour numbers and names (preset accent or a default); off: none."""
    if mode not in EMPHASIS:
        raise ValueError(f"emphasis must be one of {', '.join(EMPHASIS)}")
    if mode == "off":
        return replace(style, accent=None)
    if mode == "on" and style.accent is None:
        return replace(style, accent=DEFAULT_ACCENT)
    return style


def available() -> list[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def load_style(name_or_path: str) -> Style:
    path = Path(name_or_path)
    if not path.suffix:
        path = PRESETS_DIR / f"{name_or_path}.json"
    if not path.is_file():
        raise ValueError(f"No style {name_or_path!r}. Built-in styles: {', '.join(available())}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        style = Style(
            name=path.stem,
            font=str(raw["font"]),
            size=float(raw["size"]),
            uppercase=bool(raw.get("uppercase", False)),
            strip_punct=bool(raw.get("strip_punct", False)),
            color=raw.get("color", "#FFFFFF"),
            highlight=raw.get("highlight"),
            highlight_scale=float(raw.get("highlight_scale", 1.0)),
            outline_color=raw.get("outline_color", "#000000"),
            outline=float(raw.get("outline", 0.06)),
            shadow=float(raw.get("shadow", 0.0)),
            shadow_color=raw.get("shadow_color", "#00000080"),
            box=bool(raw.get("box", False)),
            box_color=raw.get("box_color", "#000000A0"),
            box_padding=float(raw.get("box_padding", 0.3)),
            position=raw.get("position", "bottom"),
            margin_v=float(raw.get("margin_v", 0.07)),
            pop_in=bool(raw.get("pop_in", False)),
            chunk=ChunkRules(**raw.get("chunk", {})),
            effect=str(raw.get("effect", "highlight")),
            reveal=bool(raw.get("reveal", False)),
            tilt=float(raw.get("tilt", 0.0)),
            glow=float(raw.get("glow", 0.0)),
            glow_color=raw.get("glow_color", "#00E5FF"),
            accent=raw.get("accent"),
            active_color=raw.get("active_color"),
            char_width=float(raw["char_width"]) if raw.get("char_width") is not None else None,
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"Style file {path} is missing or has a bad field: {e}") from e
    if style.position not in POSITIONS:
        raise ValueError(f"position must be one of {sorted(POSITIONS)}")
    if style.effect not in EFFECTS:
        raise ValueError(f"effect must be one of {', '.join(EFFECTS)}")
    if style.effect in ("karaoke", "boxed") and style.highlight is None:
        raise ValueError(f"The {style.effect} effect needs a highlight colour")
    if not -30 <= style.tilt <= 30 or not 0 <= style.glow <= 1:
        raise ValueError("tilt must be within ±30 degrees and glow between 0 and 1")
    if not 0.01 <= style.size <= 0.3:
        raise ValueError("size must be between 0.01 and 0.3 of the shorter side")
    for c in (style.color, style.outline_color, style.shadow_color, style.box_color, style.highlight,
              style.glow_color, style.accent, style.active_color):
        if c is not None:
            ass_color(c)  # validates
    return style
