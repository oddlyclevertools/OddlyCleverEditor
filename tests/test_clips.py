import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cliptool import clipfind, export, media, reframe, styles
from cliptool import transcript as tr


def story():
    """Sentences with timings: (text, start, end) per word, ~1 word every 0.35 s."""
    lines = [
        "So he walks in.",  # dangling start
        "Why did nobody notice the secret door?",  # strong hook
        "It was there for 200 years.",
        "Then the builders came back.",
        "They found the money.",
        "Everyone got rich.",
        "And that was that.",
    ]
    words, t = [], 0.0
    for line in lines:
        for tok in line.split():
            words.append(tr.Word(round(t, 2), round(t + 0.3, 2), tok))
            t += 0.35
        t += 0.8  # pause between sentences
    return tuple(words)


def test_sentences_split_on_punctuation():
    sents = clipfind.sentences_from_words(story())
    assert len(sents) == 7
    assert sents[1].text == "Why did nobody notice the secret door?"


def test_hook_prefers_questions_and_penalises_dangling_starts():
    s = clipfind.sentences_from_words(story())
    assert clipfind.hook_score(s[1]) > 0.8
    assert clipfind.hook_score(s[0]) < 0.3


def test_candidates_respect_length_and_do_not_overlap():
    cands = clipfind.find_candidates(story(), None, 16000, min_len=5, max_len=12)
    assert cands
    for c in cands:
        assert 5 <= c.duration <= 12
    ordered = sorted(cands, key=lambda c: c.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start
    assert cands[0].sentences[0].text.startswith("Why")  # the hook wins


def test_loudness_curve_is_normalised():
    audio = np.concatenate([np.full(16000, 0.1), np.full(16000, 0.4)]).astype(np.float32)
    curve = clipfind.loudness_curve(audio, 16000, 0.5)
    assert len(curve) == 4 and curve.max() > curve.min()


def test_judge_prompt_lists_every_candidate():
    cands = clipfind.find_candidates(story(), None, 16000, min_len=5, max_len=12)
    prompt = clipfind.judge_prompt(cands, "v.mp4", "v.ranking.json", 2, 5, 12)
    assert "v.ranking.json" in prompt and all(f"Candidate {c.id}:" in prompt for c in cands)


def test_apply_ranking_snaps_and_skips_overlaps():
    words = story()
    cands = clipfind.find_candidates(words, None, 16000, min_len=5, max_len=12)
    first = cands[0]
    ranking = [
        {"id": first.id, "title": "Secret door", "start": first.start + 0.2, "end": first.end - 0.1},
        {"id": first.id, "title": "Duplicate"},  # overlaps the first, dropped
    ]
    out = clipfind.apply_ranking(cands, ranking, words, count=5)
    assert len(out) == 1 and out[0].title == "Secret door" and out[0].source == "judge"
    starts = {s.start for s in clipfind.sentences_from_words(words)}
    assert out[0].start in starts


def test_apply_ranking_rejects_unknown_ids():
    cands = clipfind.find_candidates(story(), None, 16000, min_len=5, max_len=12)
    with pytest.raises(ValueError, match="candidate id"):
        clipfind.apply_ranking(cands, [{"id": 999}], story(), 3)


def test_load_ranking_accepts_a_pasted_chat_reply(tmp_path: Path):
    p = tmp_path / "r.json"
    p.write_text('Here you go:\n```json\n[{"id": 3, "score": 8}]\n```\n', encoding="utf-8")
    assert clipfind.load_ranking(p) == [{"id": 3, "score": 8}]


def test_slug():
    assert clipfind.slug("200 crimes you could hang for!", "x") == "200_crimes_you_could_hang_for"
    assert clipfind.slug("!!!", "clip_3") == "clip_3"


# --- reframe ---------------------------------------------------------------

def test_crop_width_is_even_9_16():
    assert reframe.crop_width(1920, 1080) == 608
    assert reframe.crop_width(1080, 1920) == 1080  # never wider than the source


def test_camera_holds_inside_dead_zone_and_eases_out():
    steady = [0.5 + 0.02 * (-1) ** i for i in range(60)]  # jitter smaller than the dead zone
    path, had = reframe._camera(steady, 0.32)
    assert had and max(path) - min(path) < 1e-9
    jump = [0.3] * 30 + [0.75] * 90
    path, _ = reframe._camera(jump, 0.32)
    steps = np.abs(np.diff(path))
    assert steps.max() <= reframe.MAX_SPEED + 1e-9  # no whip pans
    assert path[-1] > 0.6  # it got there


def test_camera_centres_shots_without_faces():
    path, had = reframe._camera([None] * 10, 0.32)
    assert not had and path == [0.5] * 10


def test_pick_face_prefers_the_followed_face():
    def face(cx, size):
        f = np.zeros(15, np.float32)
        f[0], f[2], f[3], f[14] = cx * 640 - size / 2, size, size, 0.9
        return f
    faces = np.stack([face(0.2, 50), face(0.7, 45)])
    assert reframe._pick_face(faces, None, 640) == pytest.approx(0.2)
    assert reframe._pick_face(faces, 0.7, 640) == pytest.approx(0.7)
    assert reframe._pick_face(None, 0.5, 640) is None


def test_sendcmd_only_emits_changes():
    tk = reframe.Track((10, 10, 12, 12, 12, 40), 608, 30.0, 1.0, 1)
    assert reframe.sendcmd_script(tk).splitlines() == [
        "0.0000 crop@rf x 10;", "0.0667 crop@rf x 12;", "0.1667 crop@rf x 40;"]


def test_auto_layout_needs_enough_face_coverage():
    assert reframe.Track((), 608, 30, 0.8, 3).layout("auto") == "crop"
    assert reframe.Track((), 608, 30, 0.2, 3).layout("auto") == "fit"
    assert reframe.Track((), 608, 30, 0.2, 3).layout("crop") == "crop"


# --- export ----------------------------------------------------------------

def test_clip_words_are_retimed_to_zero():
    w = (tr.Word(9.0, 9.5, "before"), tr.Word(10.0, 10.4, "in"), tr.Word(19.8, 20.3, "edge"))
    out = export.clip_words(w, 10.0, 20.0)
    assert [x.text for x in out] == ["in"]
    assert out[0].start == 0.0 and out[0].end == pytest.approx(0.4)


def test_fit_filter_centres_the_frame():
    info = media.MediaInfo(1920, 1080, 60.0, "aac", 30.0)
    f = export.video_filter("fit", info, None)
    assert "scale_cuda=1080:608" in f and "overlay_cuda=x=0:y=656" in f


def _has_cuda_filters() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-v", "error", "-init_hw_device", "cuda=g", "-filter_hw_device", "g",
                          "-f", "lavfi", "-i", "color=s=640x360:d=0.1", "-vf", "hwupload_cuda,scale_cuda=320:180",
                          "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    return out.returncode == 0


@pytest.mark.skipif(not _has_cuda_filters(), reason="needs an NVIDIA GPU with CUDA filters and NVENC")
@pytest.mark.parametrize("layout", ["crop", "fit"])
def test_render_vertical_clip(tmp_path: Path, layout: str):
    src = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=4:r=30",
                    "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src)],
                   check=True)
    info = media.probe(src)
    words = (tr.Word(1.1, 1.5, "Hello"), tr.Word(1.5, 2.0, "Shorts."))
    cw = reframe.crop_width(info.width, info.height)
    track = reframe.Track(tuple([0] * 30 + [info.width - cw] * 30), cw, 30.0, 1.0, 1)
    out = tmp_path / "clip.mp4"
    fonts = Path(export.__file__).parent / "fonts"
    used = export.render(src, info, 1.0, 3.0, words, styles.load_style("bold"), layout, track, fonts, out)
    got = media.probe(out)
    assert used == layout
    assert (got.width, got.height) == (1080, 1920)
    sar = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                          "stream=sample_aspect_ratio", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    assert sar in ("1:1", "N/A", "")  # square pixels, so players show exactly 1080x1920
    assert got.duration == pytest.approx(2.0, abs=0.15)


def test_candidates_round_trip_through_json(tmp_path: Path):
    cands = clipfind.find_candidates(story(), None, 16000, min_len=5, max_len=12)
    p = tmp_path / "c.json"
    p.write_text(clipfind.to_json(cands), encoding="utf-8")
    assert clipfind.load_candidates(p) == cands


def test_ranking_tolerates_nulls_and_skips_bad_entries():
    words = story()
    cands = clipfind.find_candidates(words, None, 16000, min_len=5, max_len=12)
    a, b = cands[0], cands[1]
    ranking = [
        {"id": a.id, "start": "soon"},  # not a number: skipped
        {"id": b.id, "start": None, "end": None, "score": None, "title": None},
    ]
    out = clipfind.apply_ranking(cands, ranking, words, 5)
    assert len(out) == 1 and out[0].id == b.id and out[0].title == ""
    with pytest.raises(ValueError):
        clipfind.apply_ranking(cands, [{"id": True}], words, 5)
    with pytest.raises(ValueError):
        clipfind.apply_ranking(cands, [{"id": [1]}], words, 5)


def test_pad_bounds_stays_clear_of_neighbouring_words():
    words = (tr.Word(0.0, 1.0, "a"), tr.Word(1.1, 2.0, "b"), tr.Word(5.0, 6.0, "c"), tr.Word(6.05, 7.0, "d"))
    start, end = clipfind.pad_bounds(1.1, 6.0, words, 10.0)
    assert 1.0 <= start < 1.1  # half the 0.1 s gap at most
    assert 6.0 <= end <= 6.025
    assert clipfind.pad_bounds(0.0, 7.0, words, 7.1) == (0.0, 7.1)


def test_manifest_keeps_earlier_renders(tmp_path: Path):
    from cliptool.clips_cmd import save_manifest

    for name in ("01_a.mp4", "02_b.mp4", "01_c.mp4"):
        (tmp_path / name).write_bytes(b"x")
    save_manifest(tmp_path, [{"file": "01_a.mp4"}, {"file": "02_b.mp4"}, {"file": "gone.mp4"}])
    merged = save_manifest(tmp_path, [{"file": "01_c.mp4"}, {"file": "02_b.mp4", "title": "new"}])
    assert [m["file"] for m in merged] == ["01_c.mp4", "02_b.mp4", "01_a.mp4"]  # gone.mp4 dropped
    assert merged[1]["title"] == "new"
    (tmp_path / "clips.json").write_text("not json")
    assert save_manifest(tmp_path, [{"file": "01_a.mp4"}]) == [{"file": "01_a.mp4"}]
