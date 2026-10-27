"""Work out a steady 9:16 crop that follows the main face, shot by shot.

Frames are decoded and downscaled on the GPU (NVDEC + scale_cuda). The small frames are then
checked for shot changes and faces (OpenCV YuNet, a ~230 KB model, a few ms per frame at 4 Hz).
The camera holds still inside a dead zone and eases toward the face, and it jumps only at cuts.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
ANALYSIS_W = 640
DETECT_HZ = 4.0
CUT_CORRELATION = 0.55  # HSV histogram correlation below this = new shot
MIN_SHOT_S = 0.5
DEAD_ZONE = 0.06  # fraction of frame width the face may wander before the camera moves
FOLLOW = 0.06  # per-frame easing toward the face once outside the dead zone
MAX_SPEED = 0.007  # fastest pan, fraction of frame width per frame (~400 px/s at 1080p30)
CROP_COVERAGE = 0.5  # 'auto' crops when faces are tracked through at least this share of the clip


@dataclass(frozen=True)
class Track:
    xs: tuple[int, ...]  # crop left edge in source pixels, one per frame
    crop_w: int
    fps: float
    coverage: float  # share of frames in shots where a face was tracked
    shots: int

    def layout(self, mode: str) -> str:
        if mode != "auto":
            return mode
        return "crop" if self.coverage >= CROP_COVERAGE else "fit"


def crop_width(src_w: int, src_h: int) -> int:
    return min(src_w, int(round(src_h * 9 / 16 / 2)) * 2)


def _frames(video: Path, start: float, dur: float, w: int, h: int):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
           "-vf", f"scale_cuda={w}:{h}:format=nv12,hwdownload,format=nv12,format=bgr24",
           "-f", "rawvideo", "-"]
    size = w * h * 3
    # stderr goes to a temp file: a full stderr pipe would block ffmpeg while we wait on stdout.
    with tempfile.TemporaryFile() as errfile:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile)
        try:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code != 0:
            errfile.seek(0)
            err = errfile.read().decode(errors="replace").strip()[-800:]
            raise RuntimeError(f"ffmpeg could not decode frames for reframing: {err}")


def _pick_face(faces: np.ndarray | None, prev_x: float | None, width: int) -> float | None:
    """Biggest confident face, preferring the one we were already following."""
    if faces is None or len(faces) == 0:
        return None
    best, best_score = None, -1.0
    for f in faces:
        cx = (f[0] + f[2] / 2) / width
        score = f[2] * f[3] * f[14]
        if prev_x is not None and abs(cx - prev_x) < 0.15:
            score *= 1.6
        if score > best_score:
            best, best_score = cx, score
    return float(best)


def _camera(targets: list[float | None], crop_frac: float) -> tuple[list[float], bool]:
    """Centre x (0..1) per frame for one shot. Returns (path, had_face)."""
    known = [(i, t) for i, t in enumerate(targets) if t is not None]
    half = crop_frac / 2
    if not known:
        return [0.5] * len(targets), False
    idx, val = zip(*known)
    filled = np.interp(np.arange(len(targets)), idx, val)  # hold the ends, interpolate gaps
    cam = float(np.median(val[: max(1, min(len(val), 4))]))
    path = []
    for t in filled:
        d = t - cam
        if abs(d) > DEAD_ZONE:
            cam += float(np.clip((d - np.sign(d) * DEAD_ZONE) * FOLLOW, -MAX_SPEED, MAX_SPEED))
        cam = float(np.clip(cam, half, 1 - half))
        path.append(cam)
    return path, True


def analyse(video: Path, start: float, end: float, src_w: int, src_h: int, fps: float) -> Track:
    import cv2

    if not MODEL.is_file():
        raise RuntimeError(f"Face model missing: {MODEL}")
    aw = ANALYSIS_W
    ah = int(round(aw * src_h / src_w / 2)) * 2
    detector = cv2.FaceDetectorYN.create(str(MODEL), "", (aw, ah), 0.6)
    every = max(1, int(round(fps / DETECT_HZ)))
    cw = crop_width(src_w, src_h)
    crop_frac = cw / src_w

    shots: list[list[float | None]] = [[]]
    prev_hist, prev_x = None, None
    for k, frame in enumerate(_frames(video, start, end - start, aw, ah)):
        hsv = cv2.cvtColor(cv2.resize(frame, (96, 54)), cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if prev_hist is not None and len(shots[-1]) >= MIN_SHOT_S * fps:
            if cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < CUT_CORRELATION:
                shots.append([])
                prev_x = None
        prev_hist = hist
        target = None
        if k % every == 0:
            _, faces = detector.detect(frame)
            target = _pick_face(faces, prev_x, aw)
            prev_x = target if target is not None else prev_x
        shots[-1].append(target)

    xs: list[int] = []
    tracked = 0
    for shot in shots:
        path, had_face = _camera(shot, crop_frac)
        tracked += len(shot) if had_face else 0
        xs.extend(int(round(min(max(c * src_w - cw / 2, 0), src_w - cw) / 2)) * 2 for c in path)
    total = max(1, len(xs))
    return Track(tuple(xs), cw, fps, tracked / total, len([s for s in shots if s]))


def sendcmd_script(track: Track, target: str = "crop@rf") -> str:
    """ffmpeg sendcmd lines that move the crop only when x changes."""
    lines, last = [], None
    for k, x in enumerate(track.xs):
        if x != last:
            lines.append(f"{k / track.fps:.4f} {target} x {x};")
            last = x
    return "\n".join(lines) + "\n"
