"""Local speech-to-text with faster-whisper (Whisper weights, MIT licence)."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

from . import gpu, media
from .transcript import Segment, Transcript, Word

log = logging.getLogger(__name__)
DEFAULT_MODEL = "large-v3-turbo"


def bundled_model(model_name: str) -> Path | None:
    """<CLIPTOOL_MODELS_DIR>/whisper-<name>/ if the installer put the model there."""
    root = os.environ.get("CLIPTOOL_MODELS_DIR")
    folder = Path(root) / f"whisper-{model_name}" if root else None
    return folder if folder and (folder / "model.bin").is_file() else None


def transcribe(video: Path, model_name: str = DEFAULT_MODEL, device: str = "cuda",
               language: str | None = None, prompt: str | None = None,
               hotwords: str | None = None, progress: Callable[[float], None] | None = None) -> Transcript:
    if device == "cuda":
        gpu.require_cuda()
    from faster_whisper import WhisperModel  # slow import; keep the CLI snappy

    info = media.probe(video)
    audio = media.load_audio(video)
    compute_type = "float16" if device == "cuda" else "int8"
    log.info("Loading Whisper %s on %s (%s)...", model_name, device, compute_type)
    bundled = bundled_model(model_name)
    if bundled:  # the Windows installer ships the model as a plain folder (short paths, no hub cache)
        model = WhisperModel(str(bundled), device=device, compute_type=compute_type)
    else:
        model = None
    try:  # offline once the model is cached; only the first run downloads it
        model = model or WhisperModel(model_name, device=device, compute_type=compute_type, local_files_only=True)
    except Exception:  # faster-whisper raises various hub errors when the cache is empty
        log.info("Model not cached yet; downloading it once from Hugging Face...")
        model = WhisperModel(model_name, device=device, compute_type=compute_type)

    started = time.time()
    raw_segments, raw_info = model.transcribe(
        audio,
        language=language,
        initial_prompt=prompt,
        hotwords=hotwords,  # every window, but in testing it once dropped 9 s of speech: opt-in only
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,  # stops Whisper repeating itself on long videos
    )
    segments = []
    last_report = 0.0
    for s in raw_segments:
        words = tuple(Word(round(w.start, 3), round(w.end, 3), w.word.strip(), round(w.probability, 3))
                      for w in (s.words or []) if w.word.strip())
        segments.append(Segment(round(s.start, 3), round(s.end, 3), s.text.strip(), words))
        if progress and info.duration > 0:
            progress(min(1.0, s.end / info.duration))
        if s.end - last_report >= 60 or s.end >= info.duration - 1:
            log.info("  transcribed %s / %s", _mmss(s.end), _mmss(info.duration))
            last_report = s.end
    log.info("Transcribed %s of audio in %.1f s (language: %s)",
             _mmss(info.duration), time.time() - started, raw_info.language)
    return Transcript(raw_info.language, round(info.duration, 3), model_name, tuple(segments))


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"
