import json
from pathlib import Path

import pytest

from cliptool import ass, styles
from cliptool import transcript as tr
from cliptool.chunking import ChunkRules, chunk_words, split_lines
from cliptool.writers import to_srt, to_vtt


def words(*specs):
    """specs: (text, start, end)"""
    return tuple(tr.Word(s, e, t) for t, s, e in specs)


SENTENCE = words(
    ("By", 0.5, 0.9), ("lunchtime", 0.98, 1.4), ("he's", 1.48, 1.65), ("got", 1.7, 1.9),
    ("a", 1.95, 2.0), ("job.", 2.05, 2.5), ("They", 4.5, 4.9), ("call", 5.06, 5.3),
    ("him", 5.32, 5.45), ("the", 5.48, 5.6), ("Red", 5.62, 5.8), ("One.", 5.85, 6.0),
)


# --- chunking ---------------------------------------------------------------

def test_breaks_after_full_stop_and_on_long_pause():
    chunks = chunk_words(SENTENCE, ChunkRules())
    assert [c.text for c in chunks] == ["By lunchtime he's got a job.", "They call him the Red One."]


def test_max_words_respected():
    chunks = chunk_words(SENTENCE, ChunkRules(max_words=3))
    assert all(len(c.words) <= 3 for c in chunks)
    assert sum(len(c.words) for c in chunks) == len(SENTENCE)


def test_chunks_never_overlap_and_stay_ordered():
    chunks = chunk_words(SENTENCE, ChunkRules(max_words=2, min_duration=2.0))
    for a, b in zip(chunks, chunks[1:]):
        assert a.start < a.end <= b.start


def test_min_duration_stretches_into_silence_only():
    w = words(("Hi.", 0.0, 0.2), ("There.", 3.0, 3.3))
    first, second = chunk_words(w, ChunkRules(min_duration=0.8))
    assert first.end == pytest.approx(0.8)
    assert second.end == pytest.approx(3.8)


def test_blank_words_are_dropped():
    w = words(("  ", 0, 0.1), ("ok", 0.1, 0.3))
    assert chunk_words(w, ChunkRules())[0].text == "ok"


def test_empty_input():
    assert chunk_words((), ChunkRules()) == ()


def test_split_lines_balances_two_lines():
    lines = split_lines("one two three four five six seven eight".split(), 20)
    assert len(lines) == 2
    assert abs(len(lines[0]) - len(lines[1])) <= 6


def test_split_lines_keeps_short_text_on_one_line():
    assert split_lines(["short", "line"], 42) == ("short line",)


# --- SRT / VTT --------------------------------------------------------------

def test_srt_format():
    srt = to_srt(chunk_words(SENTENCE, ChunkRules()))
    assert srt.startswith("1\n00:00:00,500 --> 00:00:02,500\nBy lunchtime he's got a job.\n")
    assert "\n2\n00:00:04,500 --> 00:00:06,000\nThey call him the Red One.\n" in srt


def test_srt_timestamp_over_an_hour():
    w = words(("Late.", 3725.25, 3726.25))
    assert "01:02:05,250 --> 01:02:06,250" in to_srt(chunk_words(w, ChunkRules()))


def test_vtt_header_and_dot_millis():
    vtt = to_vtt(chunk_words(SENTENCE, ChunkRules()))
    assert vtt.startswith("WEBVTT\n\n00:00:00.500 --> 00:00:02.500\n")


# --- styles and ASS ---------------------------------------------------------

def test_ass_color_conversion():
    assert styles.ass_color("#FFE14D") == "&H004DE1FF"
    assert styles.ass_color("#000000A0") == "&H5F000000"
    with pytest.raises(ValueError):
        styles.ass_color("yellow")


@pytest.mark.parametrize("name", ["bold", "clean", "minimal"])
def test_builtin_presets_load(name):
    s = styles.load_style(name)
    assert s.name == name and s.size > 0


def test_unknown_style_lists_builtins():
    with pytest.raises(ValueError, match="bold"):
        styles.load_style("nope")


def test_bold_ass_highlights_one_word_per_event():
    style = styles.load_style("bold")
    text = ass.to_ass(SENTENCE, style, 1920, 1080)
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text
    events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == len(SENTENCE)  # one event per spoken word
    for e in events:
        assert e.count("\\c&H004DE1FF") == 1
    assert "JOB" in events[5] and "job." not in text  # uppercase, punctuation stripped


def test_word_events_are_contiguous_within_a_chunk():
    style = styles.load_style("bold")
    chunk = chunk_words(SENTENCE[:3], style.chunk)[0]
    events = ass.chunk_events(chunk, style)
    ends = [e.split(",")[2] for e in events[:-1]]
    starts = [e.split(",")[1] for e in events[1:]]
    assert ends == starts


def test_minimal_style_is_one_event_per_chunk():
    style = styles.load_style("minimal")
    events = [l for l in ass.to_ass(SENTENCE, style, 1280, 720).splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 2 and "\\c" not in events[0]


def test_ass_escapes_override_braces():
    style = styles.load_style("minimal")
    text = ass.to_ass(words(("{evil}", 0, 1)), style, 640, 360)
    assert "{evil}" not in text and "(evil)" in text


# --- transcript JSON --------------------------------------------------------

def test_transcript_round_trip(tmp_path: Path):
    t = tr.Transcript("en", 6.0, "test", (tr.Segment(0.5, 6.0, "x", SENTENCE),))
    p = tmp_path / "t.json"
    tr.save(t, p)
    assert tr.load(p) == t


def test_transcript_rejects_bad_files(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"format": 1, "segments": []}))
    with pytest.raises(ValueError):
        tr.load(p)
    p.write_text(json.dumps({"format": 99}))
    with pytest.raises(ValueError, match="format"):
        tr.load(p)


def test_split_number_and_hyphen_tokens_are_glued():
    w = words(("2", 1.0, 1.2), (",000", 1.2, 1.5), ("Anglo", 2.0, 2.3), ("-Saxons.", 2.3, 2.8))
    assert chunk_words(w, ChunkRules())[0].text == "2,000 Anglo-Saxons."


def test_glue_needs_a_tight_gap():
    w = words(("Wait", 0.0, 0.3), ("-no", 1.5, 1.8))
    assert chunk_words(w, ChunkRules(max_gap=5))[0].text == "Wait -no"


def test_weak_word_moves_to_next_line_when_cut_by_length():
    w = words(("one", 0, .2), ("two", .2, .4), ("three", .4, .6), ("and", .6, .8), ("four", .8, 1), ("five.", 1, 1.2))
    chunks = chunk_words(w, ChunkRules(max_words=4))
    assert [c.text for c in chunks] == ["one two three", "and four five."]


@pytest.mark.parametrize("name", ["bold", "clean", "minimal"])
def test_portrait_lines_fit_the_width(name):
    style = styles.load_style(name)
    portrait = ass.fit_rules(style, 1080, 1920)
    landscape = ass.fit_rules(style, 1920, 1080)
    assert portrait.max_line_chars <= landscape.max_line_chars
    font_px = 1080 * style.size
    assert portrait.max_line_chars * font_px * 0.56 <= 1080  # never wider than the frame


def test_dotted_file_names_get_their_own_transcript(tmp_path):
    from cliptool.cli import _outputs
    a = _outputs(tmp_path / "ep.1.mp4", None)[0]
    b = _outputs(tmp_path / "ep.2.mp4", None)[0]
    assert a.name == "ep.1.json" and a != b


def test_bom_and_newlines_in_edited_transcript(tmp_path):
    w = words(("two\nlines", 0, 1))
    p = tmp_path / "t.json"
    tr.save(tr.Transcript("en", 1.0, "t", (tr.Segment(0, 1, "x", w),)), p)
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())  # Notepad-style BOM
    text = ass.to_ass(tr.load(p).words, styles.load_style("minimal"), 640, 360)
    assert "two lines" in text.splitlines()[-1]


def test_replace_words_keeps_punctuation_and_whole_words():
    t = tr.Transcript("en", 2, "t", (tr.Segment(0, 2, "Callan's mate Callanby.", words(("Callan's", 0, 1), ("Callanby.", 1, 2))),))
    fixed = tr.replace_words(t, tr.parse_replacements(["Callan=Callum"]))
    assert [w.text for w in fixed.words] == ["Callum's", "Callanby."]
    assert fixed.segments[0].text == "Callum's mate Callanby."


def test_replace_needs_old_equals_new():
    with pytest.raises(ValueError):
        tr.parse_replacements(["Callan"])
