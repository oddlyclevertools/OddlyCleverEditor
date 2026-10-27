"""ffmpeg/ffprobe helpers: probe, decode audio, burn captions with NVENC."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
MP4_AUDIO_OK = {"aac", "mp3", "ac3", "eac3", "alac", "opus"}
ENCODERS = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-b:v", "0"],
    "hevc_nvenc": ["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-b:v", "0",
                   "-tag:v", "hvc1"],
    "libx264": ["-c:v", "libx264", "-preset", "medium"],  # CPU fallback, only when asked for
}


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration: float
    audio_codec: str | None
    fps: float = 30.0


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} was not found on PATH. Install ffmpeg (e.g. winget install Gyan.FFmpeg).")


def probe(path: Path) -> MediaInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if audio is None:
        raise RuntimeError(f"{path} has no audio track to transcribe.")
    width, height = (int(video["width"]), int(video["height"])) if video else (0, 0)
    rotation = 0
    for sd in (video or {}).get("side_data_list", []):
        rotation = int(sd.get("rotation", rotation))
    if abs(rotation) % 180 == 90:  # phone footage stored sideways
        width, height = height, width
    return MediaInfo(width, height, float(data["format"].get("duration", 0.0)), audio.get("codec_name"),
                     _fps((video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate")))


def _fps(rate: str | None) -> float:
    try:
        num, den = (rate or "30/1").split("/")
        value = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 30.0
    return value if 1.0 <= value <= 240.0 else 30.0


def load_audio(path: Path) -> np.ndarray:
    """Decode the first audio track to 16 kHz mono float32 (what Whisper expects)."""
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
         "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode audio: {out.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(out.stdout, np.float32)


def encoder_args(encoder: str, cq: int) -> list[str]:
    if encoder not in ENCODERS:
        raise ValueError(f"encoder must be one of {sorted(ENCODERS)}")
    quality = ["-cq", str(cq)] if encoder.endswith("_nvenc") else ["-crf", str(cq)]
    return [*ENCODERS[encoder], *quality, "-pix_fmt", "yuv420p"]


Progress = Callable[[float], None]


def run_in_workdir(args: list[str], files: dict[str, str], fonts_dir: Path, out: Path,
                   what: str, duration: float = 0.0, progress: Progress | None = None) -> None:
    """Run ffmpeg in a temp dir holding `files` and a copy of the fonts.

    Filters then use plain relative names ("captions.ass"), which avoids ffmpeg's filter escaping
    of Windows paths like "C:/...". The output goes to a temp name and is renamed on success, so a
    killed run never leaves a broken file.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.resolve().with_name(f"{out.stem}.partial{out.suffix}")
    with tempfile.TemporaryDirectory(prefix="cliptool_") as tmp:
        tmpdir = Path(tmp)
        for name, text in files.items():
            (tmpdir / name).write_text(text, encoding="utf-8")
        shutil.copytree(fonts_dir, tmpdir / "fonts")
        report = ["-progress", "pipe:1", "-nostats"] if progress else ["-stats"]
        cmd = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-v", "error", *report, *args,
               "-movflags", "+faststart", str(partial)]
        try:
            if progress:
                code = _run_with_progress(cmd, tmpdir, duration, progress)
            else:
                code = subprocess.run(cmd, cwd=tmpdir).returncode
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    if code != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed while {what} (exit {code}).")
    os.replace(partial, out)


def _run_with_progress(cmd: list[str], cwd: Path, duration: float, progress: Progress) -> int:
    """Read ffmpeg's key=value progress lines from stdout (stderr stays on the console)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    try:
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration > 0 and value.lstrip("-").isdigit():
                progress(min(1.0, max(0.0, int(value) / 1e6 / duration)))
    except BaseException:
        proc.kill()  # e.g. the user cancelled: don't leave ffmpeg running
        proc.wait()
        raise
    return proc.wait()


def burn(video: Path, ass_text: str, fonts_dir: Path, out: Path, info: MediaInfo,
         encoder: str = "h264_nvenc", cq: int = 19, preview: float | None = None,
         progress: Progress | None = None) -> None:
    """Render captions onto the video. Decode on NVDEC, encode on NVENC; libass draws the text."""
    args = ["-hwaccel", "cuda"] if encoder.endswith("_nvenc") else []
    args += ["-i", str(video.resolve()), "-map", "0:v:0", "-map", "0:a:0?",
             "-vf", "subtitles=captions.ass:fontsdir=fonts", *encoder_args(encoder, cq)]
    args += ["-c:a", "copy"] if info.audio_codec in MP4_AUDIO_OK else ["-c:a", "aac", "-b:a", "192k"]
    if preview:
        args += ["-t", f"{preview:.3f}"]
    run_in_workdir(args, {"captions.ass": ass_text}, fonts_dir, out, "burning captions",
                   preview or info.duration, progress)
