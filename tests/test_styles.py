"""v0.4 caption effects, emphasis words and hook titles."""
import re
from pathlib import Path

import pytest

from cliptool import ass, styles, titles
from cliptool import transcript as tr
from cliptool.chunking import chunk_words

FONTS = Path(styles.__file__).parent / "fonts"
TEXT = "In 1666 London burned. Samuel Pepys buried his cheese in the garden."
WORDS = tuple(tr.Word(round(0.3 * i, 2), round(0.3 * i + 0.25, 2), t) for i, t in enumerate(TEXT.split()))


def dialogues(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


@pytest.mark.parametrize("name", styles.available())
def test_every_preset_loads_and_its_font_is_bundled(name: str):
    style = styles.load_style(name)
    stems = {f.stem.replace("-", "").lower() for f in FONTS.glob("*.ttf")}
    want = style.font.replace(" ", "").lower()
    assert any(s.startswith(want) or s == want + "regular" for s in stems), style.font
    assert dialogues(ass.to_ass(WORDS, style, 1080, 1920))


def test_bundled_fonts_each_have_a_licence():
    families = {f.stem.split("-")[0] for f in FONTS.glob("*.ttf")}
    licences = " ".join(f.name for f in FONTS.glob("*.txt"))
    for fam in families:
        assert fam in licences, fam


def test_emphasis_flags_numbers_and_mid_sentence_names_only():
    flags = ass.emphasis_flags(WORDS)
    marked = [w.text for w, f in zip(WORDS, flags) if f]
    assert marked == ["1666", "London", "Pepys"]  # "In" and "Samuel" start sentences


def test_emphasis_modes():
    bold = styles.load_style("bold")
    assert styles.with_emphasis(bold, "auto").accent is None
    assert styles.with_emphasis(bold, "on").accent == styles.DEFAULT_ACCENT
    assert styles.with_emphasis(styles.load_style("karaoke"), "off").accent is None
    with pytest.raises(ValueError):
        styles.with_emphasis(bold, "loud")


def test_karaoke_is_one_event_per_chunk_with_fill_tags():
    style = styles.load_style("karaoke")
    events = dialogues(ass.to_ass(WORDS, style, 1080, 1920))
    assert len(events) == len(chunk_words(WORDS, ass.fit_rules(style, 1080, 1920)))
    assert all("\\kf" in e for e in events)
    total_cs = sum(int(x) for x in re.findall(r"\\kf(\d+)", events[0]))
    assert total_cs > 0


def test_boxed_puts_a_box_layer_under_each_word_event():
    events = dialogues(ass.to_ass(WORDS, styles.load_style("boxed"), 1080, 1920))
    boxes = [e for e in events if ",WordBox," in e]
    texts = [e for e in events if ",Default," in e]
    assert len(boxes) == len(texts) == len(WORDS)
    assert all(e.startswith("Dialogue: 0,") for e in boxes) and all(e.startswith("Dialogue: 1,") for e in texts)
    assert all(e.count("\\3a&H00&") == 1 for e in boxes)  # exactly one visible box


def test_bounce_reveal_hides_words_not_yet_spoken():
    events = dialogues(ass.to_ass(WORDS, styles.load_style("bounce"), 1080, 1920))
    first = events[0]
    assert "\\1a&HFF&" in first  # later words of the chunk are hidden
    assert "\\1a&HFF&" not in events[2] or events[2].count("\\1a&HFF&") < first.count("\\1a&HFF&")


def test_neon_adds_a_blurred_glow_layer_below():
    events = dialogues(ass.to_ass(WORDS, styles.load_style("neon"), 1080, 1920))
    glow = [e for e in events if "\\blur" in e]
    assert len(glow) * 2 == len(events) and all(e.startswith("Dialogue: 0,") for e in glow)


def test_typewriter_types_every_letter():
    style = styles.load_style("typewriter")
    events = dialogues(ass.to_ass(WORDS, style, 1080, 1920))
    letters = sum(len(re.findall(r"\\ko\d+\}[^{\s\\]", e)) for e in events)
    assert letters == sum(len(w.text) for w in WORDS)


def test_tilt_survives_the_highlight_reset():
    events = dialogues(ass.to_ass(WORDS, styles.load_style("comic"), 1080, 1920))
    assert all(e.split(",,0,0,0,,", 1)[1].startswith("{\\frz-4}") for e in events)
    assert all("{\\r}" not in e for e in events)  # \r would drop the tilt mid-line


def test_bad_effect_is_refused(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"font": "Anton", "size": 0.1, "effect": "explode"}', encoding="utf-8")
    with pytest.raises(ValueError, match="effect"):
        styles.load_style(str(p))


@pytest.mark.parametrize("look", ["banner", "sticker", "outline"])
def test_hook_title_events(look: str):
    style_lines, events = titles.title_ass("The man who buried his cheese while London burned", look,
                                           1080, 1920, 30.0)
    assert style_lines.startswith("Style: Title") and len(events) == 1
    assert ",0:00:03.50," in events[0]  # 3.5 s by default
    full = ass.to_ass(WORDS, styles.load_style("bold"), 1080, 1920, style_lines, events)
    assert sum(line.startswith("Style:") for line in full.splitlines()) == 2
    assert dialogues(full)[-1] == events[0]


def test_hook_title_edge_cases():
    assert titles.title_ass("", "banner", 1080, 1920, 10) == ("", [])
    assert titles.title_ass("Hi", "none", 1080, 1920, 10) == ("", [])
    _, ev = titles.title_ass("Whole clip", "outline", 1080, 1920, 12.0, seconds=0)
    assert ",0:00:12.00," in ev[0]
    _, ev = titles.title_ass("word " * 40, "banner", 1080, 1920, 10)
    assert "…" in ev[0] and "{" not in ev[0].split("}", 1)[1]  # cut short, no stray tags
    _, ev = titles.title_ass("{\\pos(0,0)} sneaky", "banner", 1080, 1920, 10)
    assert "\\pos(0,0)" not in ev[0]
    with pytest.raises(ValueError):
        titles.title_ass("x", "spin", 1080, 1920, 10)


def test_punctuation_only_tokens_are_not_numbers():
    w = (tr.Word(0, 0.2, "Well"), tr.Word(0.2, 0.4, "—"), tr.Word(0.4, 0.6, "..."))
    assert ass.emphasis_flags(w) == [False, False, False]


def test_boxed_without_active_color_keeps_the_word_readable(tmp_path: Path):
    p = tmp_path / "b.json"
    p.write_text('{"font": "Anton", "size": 0.1, "effect": "boxed", "highlight": "#FF0000", "color": "#FFFFFF"}',
                 encoding="utf-8")
    events = dialogues(ass.to_ass(WORDS[:2], styles.load_style(str(p)), 1080, 1920))
    text_events = [e for e in events if ",Default," in e]
    assert all("\\c&H000000FF" not in e for e in text_events)  # never red text on the red box


def test_glow_layer_keeps_its_soft_alpha_after_hidden_words(tmp_path: Path):
    base = (styles.PRESETS_DIR / "neon.json").read_text(encoding="utf-8")
    p = tmp_path / "n.json"
    p.write_text(base.replace('"pop_in": true', '"pop_in": true, "reveal": true'), encoding="utf-8")
    style = styles.load_style(str(p))
    glow = [e for e in dialogues(ass.to_ass(WORDS, style, 1080, 1920)) if "\\blur" in e]
    outline_alpha = ass._alpha(style.outline_color)
    assert glow and all(f"\\3a{outline_alpha}" not in e for e in glow)


def test_one_long_word_title_shrinks_to_fit():
    styles_line, _ = titles.title_ass("https://example.com/averyveryverylongpath", "banner", 1080, 1920, 10)
    size = int(styles_line.split(",")[2])
    assert size * 0.7 * 41 <= 1080 * 0.9
