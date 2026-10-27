"""Render one short clip: cut, reframe to 9:16 (crop or fit), burn captions. GPU end to end
except libass text drawing and the tiny crop/blur steps on small frames."""
from __future__ import annotations

from pathlib import Path

from . import media, reframe, titles
from .ass import to_ass
from .styles import Style
from .transcript import Word

OUT_W, OUT_H = 1080, 1920
LAYOUTS = ("auto", "crop", "fit", "none")


def clip_words(words: tuple[Word, ...], start: float, end: float) -> tuple[Word, ...]:
    """Words inside [start, end], re-timed so the clip starts at 0."""
    return tuple(
        Word(round(max(0.0, w.start - start), 3), round(min(end, w.end) - start, 3), w.text, w.prob)
        for w in words
        if w.start >= start - 0.05 and w.end <= end + 0.05
    )


def _even(v: float) -> int:
    return int(round(v / 2)) * 2


def video_filter(layout: str, info: media.MediaInfo, track: reframe.Track | None) -> str:
    subs = "setsar=1,subtitles=captions.ass:fontsdir=fonts"  # square pixels: 608 px is not exactly 9:16 of 1080
    if layout == "none":
        return f"[0:v]{subs}[v]"
    if layout == "crop":
        x0 = track.xs[0] if track and track.xs else (info.width - reframe.crop_width(info.width, info.height)) // 2
        cw = reframe.crop_width(info.width, info.height)
        return (f"[0:v]sendcmd=f=crop.cmd,crop@rf=w={cw}:h={info.height}:x={x0}:y=0,"
                f"hwupload_cuda,scale_cuda={OUT_W}:{OUT_H}:format=yuv420p,"
                f"hwdownload,format=yuv420p,{subs}[v]")
    # fit: the whole frame across the width, over a blurred, darkened fill of itself.
    fg_h = _even(OUT_W * info.height / info.width)
    bg_crop = reframe.crop_width(info.width, info.height)
    return (f"[0:v]split=2[a][b];"
            f"[a]crop={bg_crop}:{info.height},scale=216:384,boxblur=8:2,eq=brightness=-0.12,"
            f"hwupload_cuda,scale_cuda={OUT_W}:{OUT_H}:format=yuv420p[bg];"
            f"[b]hwupload_cuda,scale_cuda={OUT_W}:{fg_h}:format=yuv420p[fg];"
            f"[bg][fg]overlay_cuda=x=0:y={(OUT_H - fg_h) // 2},hwdownload,format=yuv420p,{subs}[v]")


def render(video: Path, info: media.MediaInfo, start: float, end: float, words: tuple[Word, ...],
           style: Style, layout: str, track: reframe.Track | None, fonts_dir: Path, out: Path,
           encoder: str = "h264_nvenc", cq: int = 19, progress: media.Progress | None = None,
           title: str = "", title_look: str = "none", title_secs: float = 3.5) -> str:
    """Returns the layout actually used. A hook title is drawn on top when title_look isn't 'none'."""
    if layout == "auto":
        layout = track.layout("auto") if track else "fit"
    if info.width <= info.height:  # already vertical: just cut and caption
        layout = "none"
    if layout not in ("crop", "fit", "none"):
        raise ValueError(f"layout must be one of {LAYOUTS}")
    out_w, out_h = (info.width, info.height) if layout == "none" else (OUT_W, OUT_H)
    title_styles, title_events = titles.title_ass(title, title_look, out_w, out_h, end - start, title_secs,
                                                  style.highlight or "#FFE14D")
    files = {"captions.ass": to_ass(clip_words(words, start, end), style, out_w, out_h,
                                    title_styles, title_events)}
    if layout == "crop":
        files["crop.cmd"] = reframe.sendcmd_script(track)
    dur = end - start
    fade_out = max(0.0, dur - 0.25)
    args = ["-init_hw_device", "cuda=gpu", "-filter_hw_device", "gpu", "-hwaccel", "cuda",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video.resolve()),
            "-filter_complex", video_filter(layout, info, track),
            "-map", "[v]", "-map", "0:a:0?",
            *media.encoder_args(encoder, cq),
            "-af", f"afade=t=in:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.25",
            "-c:a", "aac", "-b:a", "192k"]
    media.run_in_workdir(args, files, fonts_dir, out, f"rendering clip {out.name}", dur, progress)
    return layout
