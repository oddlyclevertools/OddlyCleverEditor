"""Command line: oddlyclever (alias cliptool) transcribe | burn | caption | clips | ui | styles."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import APP_NAME, COMMAND, __version__, clips_cmd, media, styles, titles
from . import transcript as tr
from .ass import to_ass
from .chunking import ChunkRules, chunk_words
from .writers import to_srt, to_vtt

log = logging.getLogger("cliptool")


def default_workspace() -> Path:
    """~/OddlyCleverEditor-projects, or the old ~/cliptool-projects if that already holds projects."""
    old = Path.home() / "cliptool-projects"
    return old if old.is_dir() else Path.home() / "OddlyCleverEditor-projects"


FONTS_DIR = Path(__file__).resolve().parent / "fonts"
SUBTITLE_RULES = ChunkRules()  # SRT/VTT for uploading: up to 2 lines of 42 characters


def _outputs(video: Path, out_dir: Path | None) -> tuple[Path, Path, Path]:
    folder = out_dir or video.parent
    folder.mkdir(parents=True, exist_ok=True)
    return tuple(folder / f"{video.stem}{ext}" for ext in (".json", ".srt", ".vtt"))


def cmd_transcribe(args: argparse.Namespace) -> Path:
    from .transcribe import transcribe

    json_path, srt_path, vtt_path = _outputs(args.video, args.out_dir)
    t = transcribe(args.video, args.model, args.device, args.language, args.prompt, args.hotwords)
    t = tr.replace_words(t, tr.parse_replacements(args.replace))
    tr.save(t, json_path)
    write_subtitles(t, srt_path, vtt_path)
    log.info("Wrote %s, %s, %s", json_path.name, srt_path.name, vtt_path.name)
    return json_path


def write_subtitles(t: tr.Transcript, srt_path: Path, vtt_path: Path) -> None:
    chunks = chunk_words(t.words, SUBTITLE_RULES)
    srt_path.write_text(to_srt(chunks, SUBTITLE_RULES.max_line_chars), encoding="utf-8")
    vtt_path.write_text(to_vtt(chunks, SUBTITLE_RULES.max_line_chars), encoding="utf-8")


def cmd_burn(args: argparse.Namespace, json_path: Path | None = None) -> Path:
    json_path = json_path or args.transcript or _outputs(args.video, args.out_dir)[0]
    if not json_path.is_file():
        raise RuntimeError(f"No transcript at {json_path}. Run 'oddlyclever transcribe' first.")
    t = tr.load(json_path)
    style = styles.with_emphasis(styles.load_style(args.style), args.emphasis)
    info = media.probe(args.video)
    if info.width == 0:
        raise RuntimeError(f"{args.video} has no video track to burn captions onto.")
    out = args.out or (args.out_dir or args.video.parent) / f"{args.video.stem}_{style.name}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not t.words:
        log.warning("Warning: the transcript has no words, so the video will have no captions.")
    ass_text = to_ass(t.words, style, info.width, info.height)
    out.with_name(f"{out.stem}.ass").write_text(ass_text, encoding="utf-8")
    log.info("Burning '%s' captions with %s -> %s", style.name, args.encoder, out.name)
    started = time.time()
    media.burn(args.video, ass_text, FONTS_DIR, out, info, args.encoder, args.quality, args.preview)
    log.info("Done in %.1f s", time.time() - started)
    return out


def ensure_transcript(args: argparse.Namespace) -> Path:
    """Reuse <video>.json if it exists (refreshing SRT/VTT from it), else transcribe."""
    json_path = args.transcript or _outputs(args.video, args.out_dir)[0]
    if args.retranscribe or not json_path.is_file():
        return cmd_transcribe(args)
    log.info("Reusing transcript %s (pass --retranscribe to redo it)", json_path.name)
    t = tr.load(json_path)
    mapping = tr.parse_replacements(args.replace)
    if mapping:
        t = tr.replace_words(t, mapping)
        tr.save(t, json_path)
        log.info("Applied %d replacement(s) to %s", len(mapping), json_path.name)
    write_subtitles(t, json_path.with_name(f"{json_path.stem}.srt"), json_path.with_name(f"{json_path.stem}.vtt"))
    return json_path


def cmd_caption(args: argparse.Namespace) -> None:
    cmd_burn(args, ensure_transcript(args))


def cmd_ui(args: argparse.Namespace) -> None:
    from .ui.server import serve

    serve(args.workspace, args.port, not args.no_browser)


def cmd_styles(_: argparse.Namespace) -> None:
    for name in styles.available():
        s = styles.load_style(name)
        desc = json.loads((styles.PRESETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
        print(f"{name:11s} {s.font:20s} {desc.get('description', '')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=COMMAND, description=f"{APP_NAME}: local GPU captions and Shorts for video creators.")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_video(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("video", type=Path, help="input video (or audio, for transcribe)")
        sp.add_argument("--out-dir", type=Path, help="where outputs go (default: next to the video)")

    def add_transcribe_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--model", default="large-v3-turbo", help="Whisper model (default large-v3-turbo)")
        sp.add_argument("--language", help="force a language code, e.g. en (default: detect)")
        sp.add_argument("--prompt", help="names or jargon to help spelling, e.g. 'Iceni, Boudica' (first 30 s only)")
        sp.add_argument("--hotwords", help="like --prompt but for every 30 s window; can drop speech, check the result")
        sp.add_argument("--replace", action="append", metavar="OLD=NEW",
                        help="fix a recurring word in the transcript, e.g. --replace Callan=Callum (repeatable)")
        sp.add_argument("--device", default="cuda", choices=["cuda", "cpu"])

    def add_render_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--style", default="bold", help=f"preset name or JSON path ({', '.join(styles.available())})")
        sp.add_argument("--transcript", type=Path, help="transcript JSON (default: <video>.json)")
        sp.add_argument("--encoder", default="h264_nvenc", choices=sorted(media.ENCODERS))
        sp.add_argument("--quality", type=int, default=19, help="NVENC CQ / x264 CRF (lower = better)")
        sp.add_argument("--emphasis", default="auto", choices=styles.EMPHASIS,
                        help="colour numbers and names: the style's choice (auto), on or off")

    def add_burn_opts(sp: argparse.ArgumentParser) -> None:
        add_render_opts(sp)
        sp.add_argument("--out", type=Path, help="output video path")
        sp.add_argument("--preview", type=float, help="only render the first N seconds")

    sp = sub.add_parser("transcribe", help="video -> transcript JSON + SRT + VTT")
    add_video(sp)
    add_transcribe_opts(sp)
    sp.set_defaults(func=cmd_transcribe)

    sp = sub.add_parser("burn", help="burn styled captions from a transcript JSON")
    add_video(sp)
    add_burn_opts(sp)
    sp.set_defaults(func=cmd_burn)

    sp = sub.add_parser("caption", help="transcribe (if needed) and burn, in one go")
    add_video(sp)
    add_transcribe_opts(sp)
    add_burn_opts(sp)
    sp.add_argument("--retranscribe", action="store_true", help="ignore an existing transcript JSON")
    sp.set_defaults(func=cmd_caption)

    sp = sub.add_parser("clips", help="find the best short clips and export them as 9:16 Shorts")
    add_video(sp)
    add_transcribe_opts(sp)
    add_render_opts(sp)
    sp.add_argument("--retranscribe", action="store_true", help="ignore an existing transcript JSON")
    sp.add_argument("--count", type=int, default=5, help="how many clips (default 5)")
    sp.add_argument("--min", dest="min_len", type=float, default=20.0, help="shortest clip, seconds")
    sp.add_argument("--max", dest="max_len", type=float, default=60.0, help="longest clip, seconds")
    sp.add_argument("--layout", default="auto", choices=["auto", "crop", "fit", "none"],
                    help="9:16 framing: follow faces (crop), whole frame on a blur (fit), or auto")
    sp.add_argument("--judge", action="store_true",
                    help="write a prompt for an LLM (e.g. Claude Code) to rank the candidates, then stop")
    sp.add_argument("--ranking", type=Path, help="JSON ranking from the judge (see --judge)")
    sp.add_argument("--judge-url", help="judge with an OpenAI-compatible server you run, e.g. "
                    "http://127.0.0.1:11434/v1 (Ollama); key, if needed, from CLIPTOOL_JUDGE_KEY")
    sp.add_argument("--judge-model", default="", help="model name for --judge-url, e.g. qwen3:8b")
    sp.add_argument("--no-render", action="store_true", help="only list the clips, don't render them")
    sp.add_argument("--title-look", default="banner", choices=titles.LOOKS,
                    help="animated hook title at the top of each Short (uses the judge's clip title)")
    sp.add_argument("--title-secs", type=float, default=3.5, help="how long the title stays; 0 = whole clip")
    sp.set_defaults(func=clips_cmd.run)

    sp = sub.add_parser("ui", help="open the local browser UI (drag and drop, caption editor, clips)")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--workspace", type=Path, default=default_workspace(),
                    help="where projects and their outputs are kept (default ~/OddlyCleverEditor-projects)")
    sp.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("styles", help="list the built-in caption styles")
    sp.set_defaults(func=cmd_styles)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "video", None) is not None and not args.video.is_file():
            raise RuntimeError(f"File not found: {args.video}")
        media.require_tools()
        args.func(args)
    except KeyboardInterrupt:
        log.error("Stopped.")
        return 130
    except (RuntimeError, ValueError, OSError) as e:
        log.error("Error: %s", e)
        return 1
    except Exception as e:  # e.g. a model download failing while offline
        log.debug("Unexpected error", exc_info=True)
        log.error("Error: %s: %s", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
