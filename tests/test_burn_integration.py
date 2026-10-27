"""End-to-end burn on a generated 2 s clip (needs ffmpeg; uses NVENC if available)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cliptool import cli, media, styles
from cliptool import transcript as tr

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _has_nvenc() -> bool:
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x144:d=0.1",
                          "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    return out.returncode == 0


@pytest.mark.parametrize("style", styles.available())
def test_burn_writes_a_playable_video(tmp_path: Path, style: str):
    video = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(video)], check=True)
    words = (tr.Word(0.1, 0.6, "Hello"), tr.Word(0.6, 1.2, "creator."))
    tr.save(tr.Transcript("en", 2.0, "test", (tr.Segment(0.1, 1.2, "Hello creator.", words),)),
            tmp_path / "in.json")
    encoder = "h264_nvenc" if _has_nvenc() else "libx264"
    out = tmp_path / "out.mp4"
    assert cli.main(["burn", str(video), "--style", style, "--encoder", encoder, "--out", str(out)]) == 0
    info = media.probe(out)
    assert (info.width, info.height) == (640, 360)
    assert info.duration == pytest.approx(2.0, abs=0.1)
    plain = re.sub(r"\{[^}]*\}", "", out.with_name("out.ass").read_text(encoding="utf-8"))
    assert "Hello" in plain.title()


def test_missing_transcript_is_a_clear_error(tmp_path: Path, caplog):
    video = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=1",
                    "-f", "lavfi", "-i", "sine=d=1", "-shortest", str(video)], check=True)
    assert cli.main(["burn", str(video)]) == 1
    assert "Run 'oddlyclever transcribe' first" in caplog.text
