"""Styled, word-highlighted captions as an ASS subtitle file (rendered by libass).

Effects (style.effect):
  highlight   the spoken word changes colour (and pops if pop_in)
  bounce      the spoken word springs in with overshoot
  karaoke     a colour fill sweeps through each word as it is spoken
  boxed       the spoken word sits on a coloured box that jumps word to word
  typewriter  letters appear one by one
Any effect can add reveal (words appear once spoken), tilt, glow and accent (emphasis colour).
"""
from __future__ import annotations

import re
from dataclasses import replace

from .chunking import Chunk, ChunkRules, chunk_words, split_lines
from .styles import POSITIONS, Style, ass_color
from .transcript import Word

_EDGE_PUNCT = re.compile(r"^[\"'“”‘’«»(\[]+|[\"'“”‘’«»)\].,!?;:…—–-]+$")
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
_NOT_NAMES = {"I", "I'm", "I'll", "I've", "I'd", "OK", "Okay", "Oh", "Mr", "Mrs", "Ms", "Dr"}
_HIDDEN = "\\1a&HFF&\\3a&HFF&\\4a&HFF&"


def _ts(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    text = " ".join(text.split())  # an edited word must not break the Dialogue line
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _alpha(hex_rgba: str) -> str:
    """The transparency part of a colour, as an ASS alpha value (&HAA&)."""
    return f"&H{ass_color(hex_rgba)[2:4]}&"


def display_text(word: str, style: Style) -> str:
    text = _EDGE_PUNCT.sub("", word) if style.strip_punct else word
    return text.upper() if style.uppercase else text


def emphasis_flags(words: tuple[Word, ...], previous: str = "") -> list[bool]:
    """True for words worth an accent colour: numbers, money, years and names (capitalised
    mid-sentence). `previous` is the text of the word before these, to spot sentence starts."""
    flags = []
    prev = previous
    for w in words:
        core = _EDGE_PUNCT.sub("", w.text)
        starts_sentence = not prev or bool(_SENTENCE_END.search(prev))
        is_number = any(ch.isdigit() for ch in core) or (bool(core) and core[:1] in "$£€")
        is_name = (not starts_sentence and len(core) > 1 and core[:1].isupper()
                   and core not in _NOT_NAMES)
        flags.append(is_number or is_name)
        prev = w.text
    return flags


def _style_line(name: str, style: Style, font_px: int, width: int, height: int, *, primary: str,
                border_style: int, outline: float, outline_color: str, shadow: float) -> str:
    fields = [
        name, style.font, font_px,
        ass_color(primary), ass_color(primary), ass_color(outline_color),
        ass_color(style.shadow_color),
        0, 0, 0, 0, 100, 100, 0, 0,
        border_style, f"{outline:.1f}", f"{shadow:.1f}",
        POSITIONS[style.position], round(width * 0.06), round(width * 0.06),
        round(height * style.margin_v), 1,
    ]
    return f"Style: {','.join(str(f) for f in fields)}\n"


def header(style: Style, width: int, height: int, extra_styles: str = "") -> str:
    font_px = round(min(width, height) * style.size)
    if style.box:
        main = _style_line("Default", style, font_px, width, height, primary=style.color, border_style=3,
                           outline=font_px * style.box_padding, outline_color=style.box_color,
                           shadow=font_px * style.shadow)
    else:
        main = _style_line("Default", style, font_px, width, height, primary=style.color, border_style=1,
                           outline=font_px * style.outline, outline_color=style.outline_color,
                           shadow=font_px * style.shadow)
    if style.effect == "boxed":
        # Opaque box per glyph; only the spoken word's box is made visible (\3a per word).
        main += _style_line("WordBox", style, font_px, width, height, primary="#00000000", border_style=3,
                            outline=font_px * 0.16, outline_color=style.highlight or "#FFE14D", shadow=0)
    return (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{main}{extra_styles}\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def fit_rules(style: Style, width: int, height: int) -> ChunkRules:
    """Shrink the style's line length to what fits the frame (matters for 9:16 video)."""
    font_px = min(width, height) * style.size
    char_px = font_px * (style.char_width or (0.72 if style.uppercase else 0.56))  # rough average glyph width
    fits = max(6, int(width * 0.88 / char_px))
    line = min(style.chunk.max_line_chars, fits)
    return replace(style.chunk, max_line_chars=line, max_chars=min(style.chunk.max_chars, 2 * line))


def _pop_tags(style: Style) -> str:
    scale = round(style.highlight_scale * 100)
    if style.effect == "bounce":
        return (f"\\fscx{scale - 30}\\fscy{scale - 30}\\t(0,80,\\fscx{scale + 14}\\fscy{scale + 14})"
                f"\\t(80,170,\\fscx{scale}\\fscy{scale})")
    if style.pop_in:
        return f"\\fscx{scale - 12}\\fscy{scale - 12}\\t(0,90,\\fscx{scale}\\fscy{scale})"
    return f"\\fscx{scale}\\fscy{scale}"


class _Line:
    """The tokens of one chunk plus how to lay them out."""

    def __init__(self, chunk: Chunk, style: Style, max_line_chars: int | None, flags: list[bool] | None,
                 font_px: float):
        self.font_px = font_px
        tokens = [_escape(display_text(w.text, style)) for w in chunk.words]
        keep = [i for i, t in enumerate(tokens) if t]
        self.tokens = [tokens[i] for i in keep]
        self.words = [chunk.words[i] for i in keep]
        self.emph = [bool(flags[i]) for i in keep] if flags else [False] * len(keep)
        lines = split_lines(self.tokens, max_line_chars or style.chunk.max_line_chars) if self.tokens else []
        self.first_line_count = len(lines[0].split(" ")) if len(lines) == 2 else len(self.tokens)

    def sep(self, i: int) -> str:
        if i == 0:
            return ""
        return "\\N" if i == self.first_line_count else " "


def _base_tags(style: Style) -> str:
    return f"{{\\frz{style.tilt:g}}}" if style.tilt else ""


def _event(layer: int, start: float, end: float, text: str, name: str = "Default") -> str:
    return f"Dialogue: {layer},{_ts(start)},{_ts(end)},{name},,0,0,0,,{text}"


def _with_glow(style: Style, events: list[str], font_px: float) -> list[str]:
    """Neon: a blurred, coloured copy of each text event, one layer underneath it."""
    if not style.glow:
        return events
    g = f"{style.glow * font_px:.1f}"
    glow = f"{{\\bord{g}\\blur{g}\\3c{ass_color(style.glow_color)}\\3a&H30&\\shad0}}"
    keep_glow = f"\\3a{_alpha(style.outline_color)}"
    out = []
    for e in events:
        if e.startswith("Dialogue: 1,") and ",Default,," in e:
            head, text = e.split(",,0,0,0,,", 1)
            text = text.replace(keep_glow, "\\3a&H30&")  # a reset after a hidden word mustn't dim the glow
            out.append(f"Dialogue: 0,{head[len('Dialogue: 1,'):]},,0,0,0,,{glow}{text}")
        out.append(e)
    return out


def _word_events(line: _Line, chunk: Chunk, style: Style) -> list[str]:
    """highlight / bounce / boxed: one event per spoken word."""
    color = ass_color(style.color)
    accent = ass_color(style.accent) if style.accent else None
    reset_alpha = f"\\1a{_alpha(style.color)}\\3a{_alpha(style.outline_color)}\\4a{_alpha(style.shadow_color)}"

    def text_for(active: int | None) -> str:
        parts = [_base_tags(style)]
        for i, tok in enumerate(line.tokens):
            parts.append(line.sep(i))
            tags = ""
            if active is not None and style.reveal and i > active:
                tags = _HIDDEN
            elif i == active and style.highlight:
                fill = (style.active_color or style.color) if style.effect == "boxed" else style.highlight
                tags = f"\\c{ass_color(fill)}{_pop_tags(style)}"
                if style.effect == "boxed":
                    tags += "\\bord0\\shad0"
            elif line.emph[i] and accent:
                tags = f"\\c{accent}"
            if tags:
                reset = f"\\c{color}\\fscx100\\fscy100"
                if _HIDDEN in tags:
                    reset += reset_alpha
                if "\\bord0" in tags:
                    reset += f"\\bord{style.outline * line.font_px:.1f}\\shad{style.shadow * line.font_px:.1f}"
                parts.append(f"{{{tags}}}{tok}{{{reset}}}")
            else:
                parts.append(tok)
        return "".join(parts)

    def box_for(active: int) -> str:
        parts = [_base_tags(style), "{\\3a&HFF&}"]
        for i, tok in enumerate(line.tokens):
            parts.append(line.sep(i))
            if i == active:
                parts.append(f"{{\\3a&H00&{_pop_tags(style)}}}{tok}{{\\3a&HFF&\\fscx100\\fscy100}}")
            else:
                parts.append(tok)
        return "".join(parts)

    layer = 1 if style.glow or style.effect == "boxed" else 0
    if style.highlight is None and not style.reveal:
        return [_event(layer, chunk.start, chunk.end, text_for(None))]
    events = []
    for i, w in enumerate(line.words):
        start = chunk.start if i == 0 else w.start
        end = line.words[i + 1].start if i + 1 < len(line.words) else chunk.end
        if end > start:
            if style.effect == "boxed":
                events.append(_event(0, start, end, box_for(i), "WordBox"))
            events.append(_event(layer, start, end, text_for(i)))
    return events


def _cs(seconds: float) -> int:
    return max(0, round(seconds * 100))


def _karaoke_event(line: _Line, chunk: Chunk, style: Style) -> list[str]:
    color = ass_color(style.color)
    accent = ass_color(style.accent) if style.accent else None
    parts = [_base_tags(style), f"{{\\1c{ass_color(style.highlight)}\\2c{color}}}"]
    lead = _cs(line.words[0].start - chunk.start) if line.words else 0
    if lead:
        parts.append(f"{{\\k{lead}}}")
    for i, (tok, w) in enumerate(zip(line.tokens, line.words)):
        nxt = line.words[i + 1].start if i + 1 < len(line.words) else w.end
        dur = max(1, _cs(nxt - w.start))
        unsung = f"\\2c{accent}" if line.emph[i] and accent else ""
        after = f"{{\\2c{color}}}" if unsung else ""
        parts.append(f"{line.sep(i)}{{\\kf{dur}{unsung}}}{tok}{after}")
    layer = 1 if style.glow else 0
    return [_event(layer, chunk.start, chunk.end, "".join(parts))]


def _typewriter_event(line: _Line, chunk: Chunk, style: Style) -> list[str]:
    """\\ko keeps a letter (fill and outline) hidden until its turn; \\2a makes the waiting fill clear."""
    accent = ass_color(style.accent) if style.accent else None
    color = ass_color(style.color)
    parts = [_base_tags(style), "{\\2a&HFF&\\4a&HFF&}"]
    clock = chunk.start
    for i, (tok, w) in enumerate(zip(line.tokens, line.words)):
        sep = line.sep(i)
        gap = _cs(w.start - clock)
        if sep:
            parts.append(f"{{\\ko{gap}}}{sep}" if sep == " " else f"{sep}{{\\ko{gap}}}")
        elif gap:
            parts.append(f"{{\\ko{gap}}}")
        clock = max(clock, w.start)
        word_cs = max(len(tok), _cs(max(w.end, w.start + 0.05) - w.start))
        per = max(1, min(6, word_cs // max(1, len(tok))))
        if line.emph[i] and accent:
            parts.append(f"{{\\1c{accent}}}")
        parts.append("".join(f"{{\\ko{per}}}{ch}" for ch in tok))
        if line.emph[i] and accent:
            parts.append(f"{{\\1c{color}}}")
        clock += per * len(tok) / 100
    # Shadows can't follow \ko per letter, so they are dropped (\4a above); the outline carries the contrast.
    layer = 1 if style.glow else 0
    return [_event(layer, chunk.start, chunk.end, "".join(parts))]


def chunk_events(chunk: Chunk, style: Style, max_line_chars: int | None = None,
                 flags: list[bool] | None = None, font_px: float = 54.0) -> list[str]:
    line = _Line(chunk, style, max_line_chars, flags, font_px)
    if not line.tokens:
        return []
    if style.effect == "karaoke":
        events = _karaoke_event(line, chunk, style)
    elif style.effect == "typewriter":
        events = _typewriter_event(line, chunk, style)
    else:
        events = _word_events(line, chunk, style)
    return _with_glow(style, events, font_px)


def caption_events(words: tuple[Word, ...], style: Style, width: int, height: int) -> list[str]:
    rules = fit_rules(style, width, height)
    font_px = round(min(width, height) * style.size)
    events: list[str] = []
    previous = ""
    for c in chunk_words(words, rules):
        flags = emphasis_flags(c.words, previous) if style.accent else None
        events.extend(chunk_events(c, style, rules.max_line_chars, flags, font_px))
        previous = c.words[-1].text if c.words else previous
    return events


def to_ass(words: tuple[Word, ...], style: Style, width: int, height: int,
           extra_styles: str = "", extra_events: list[str] | None = None) -> str:
    events = caption_events(words, style, width, height) + list(extra_events or [])
    return header(style, width, height, extra_styles) + "\n".join(events) + "\n"
