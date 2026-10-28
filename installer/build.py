"""Build the full offline Windows installer: dist/OddlyCleverEditor-setup-<version>.exe (Inno Setup).

Everything goes inside: a private Python, the exact package versions from the dev venv, ffmpeg,
and the default Whisper model. Run from clip-tool/:

    .venv/Scripts/python.exe installer/build.py            # one setup.exe
    .venv/Scripts/python.exe installer/build.py --split    # setup.exe + .bin parts, for GitHub Releases

Needs Inno Setup 6 (ISCC.exe) and uv. Downloads are cached in installer/_cache.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / "_cache"
STAGE = HERE / "_stage"
DIST = HERE / "dist"
sys.path.insert(0, str(ROOT))
from cliptool import __version__  # noqa: E402

PY_VERSION = "3.12.10"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PY_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
MODEL_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"  # what faster-whisper maps large-v3-turbo to
GITHUB_MAX = 2 * 1024**3  # GitHub Releases refuse files of 2 GiB or more
MAX_DIR = 110  # longest install folder the installer accepts (see cliptool.iss)
DEV_ONLY = {"pytest", "pytest-cov", "coverage", "iniconfig", "pluggy", "pygments", "cliptool"}
ISCC_PLACES = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
               Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")]


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, **kw)


def fetch(url: str, sha256: str) -> Path:
    import hashlib

    CACHE.mkdir(exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[1]
    if not path.is_file():
        with urllib.request.urlopen(url, timeout=120) as res, path.open("wb") as f:  # noqa: S310 - fixed https URL
            shutil.copyfileobj(res, f)
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != sha256:
        path.unlink()
        raise SystemExit(f"Checksum mismatch for {path.name}: {got}")
    return path


def stage_python() -> Path:
    step(f"Python {PY_VERSION} (embeddable)")
    py_dir = STAGE / "python"
    with zipfile.ZipFile(fetch(PY_URL, PY_SHA256)) as z:
        z.extractall(py_dir)
    pth = next(py_dir.glob("python*._pth"))
    lines = [l for l in pth.read_text().splitlines() if l.strip() and not l.startswith("#")]
    pth.write_text("\n".join(lines + ["Lib\\site-packages", "import site"]) + "\n")
    return py_dir


def stage_packages(py_dir: Path) -> None:
    step("Packages (pinned to the tested dev venv)")
    venv_py = ROOT / ".venv/Scripts/python.exe"
    frozen = run(["uv", "pip", "freeze", "--python", str(venv_py)], capture_output=True, text=True).stdout
    pins = [l for l in frozen.splitlines() if l and not l.startswith("-e")
            and l.split("==")[0].lower() not in DEV_ONLY]
    req = STAGE / "_requirements.txt"
    req.write_text("\n".join(pins) + "\n")
    target = py_dir / "Lib/site-packages"
    common = ["--target", str(target), "--python-version", "3.12", "--python-platform", "x86_64-pc-windows-msvc"]
    run(["uv", "pip", "install", *common, "-r", str(req)])
    run(["uv", "pip", "install", *common, "--no-deps", "--reinstall", str(ROOT)])
    req.unlink()
    # The launcher runs `python -m cliptool`; no console-script stubs needed.
    shutil.rmtree(target / "bin", ignore_errors=True)


def stage_ffmpeg() -> None:
    step("ffmpeg (GPL build, with its licence)")
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg isn't on PATH.")
    bin_dir = Path(exe).resolve().parent
    dest = STAGE / "ffmpeg"
    dest.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(bin_dir / name, dest / name)
    for name in ("LICENSE", "README.txt"):
        if (bin_dir.parent / name).is_file():
            shutil.copy2(bin_dir.parent / name, dest / f"FFMPEG-{name}")


def stage_model() -> None:
    step(f"Whisper model {MODEL_REPO}")
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache/huggingface/hub")
    src = hub / f"models--{MODEL_REPO.replace('/', '--')}"
    if not (src / "snapshots").is_dir():
        raise SystemExit(f"Model not cached at {src}. Transcribe something once first.")
    snap = max((src / "snapshots").iterdir(), key=lambda d: d.stat().st_mtime)
    # A plain folder, not the hub cache layout: its 40-character snapshot hashes made paths too
    # long for Windows (MAX_PATH) when the user picks a deep install folder.
    shutil.copytree(snap, STAGE / "models" / "whisper-large-v3-turbo")


def make_icon() -> Path:
    """The app logo (gradient rounded square, play mark, caption bar) as a .ico."""
    import cv2
    import numpy as np

    n = 256
    y, x = np.mgrid[0:n, 0:n] / (n - 1)
    t = ((x + y) / 2)[..., None]
    top, bottom = np.array([77, 225, 255]), np.array([138, 90, 255])  # BGR: #ffe14d -> #ff5a8a
    img = np.zeros((n, n, 4), np.uint8)
    img[..., :3] = (top * (1 - t) + bottom * t).astype(np.uint8)
    mask = np.zeros((n, n), np.uint8)
    r = 64
    cv2.rectangle(mask, (16 + r, 16), (240 - r, 240), 255, -1)
    cv2.rectangle(mask, (16, 16 + r), (240, 240 - r), 255, -1)
    for cx, cy in ((16 + r, 16 + r), (240 - r, 16 + r), (16 + r, 240 - r), (240 - r, 240 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, cv2.LINE_AA)
    img[..., 3] = mask
    ink = (26, 18, 20, 255)
    cv2.fillPoly(img, [np.array([[96, 76], [96, 148], [156, 112]], np.int32)], ink, cv2.LINE_AA)
    cv2.rectangle(img, (64, 172), (192, 198), ink, -1, cv2.LINE_AA)
    png = STAGE / "_icon.png"
    cv2.imwrite(str(png), img)
    ico = STAGE / "oddlyclever.ico"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-vf", "scale=256:256", str(ico)])
    png.unlink()
    return ico


LAUNCHER = r"""@echo off
setlocal
set "HERE=%~dp0"
set "PATH=%HERE%ffmpeg;%PATH%"
set "CLIPTOOL_MODELS_DIR=%HERE%models"
set "HF_HUB_CACHE=%HERE%models\hub"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
if not "%~1"=="" goto cli
title OddlyCleverEditor
echo OddlyCleverEditor {version} is starting. Your browser opens in a moment.
echo Keep this window open while you work. Close it to stop OddlyCleverEditor.
echo.
"%HERE%python\python.exe" -m cliptool ui
if errorlevel 1 pause
goto :eof
:cli
"%HERE%python\python.exe" -m cliptool %*
"""


def stage_docs() -> None:
    step("Launcher, icon and licences")
    (STAGE / "oddlyclever.cmd").write_text(LAUNCHER.replace("{version}", __version__), encoding="ascii")
    for name in ("LICENSE", "TRADEMARKS.md", "THIRD_PARTY.md", "README.md"):
        shutil.copy2(ROOT / name, STAGE / name)
    make_icon()


def smoke_test(py_dir: Path) -> None:
    step("Smoke test of the staged app")
    env = {**os.environ, "PATH": f"{STAGE / 'ffmpeg'};{os.environ['PATH']}", "PYTHONNOUSERSITE": "1",
           "HF_HUB_CACHE": str(STAGE / "models/hub"), "CLIPTOOL_MODELS_DIR": str(STAGE / "models")}
    run([str(py_dir / "python.exe"), "-m", "cliptool", "--version"], env=env)
    run([str(py_dir / "python.exe"), "-c",
         "import ctranslate2, cv2, faster_whisper, numpy; from cliptool import gpu; gpu.register_cuda_dlls(); "
         "print('CUDA devices:', ctranslate2.get_cuda_device_count())"], env=env)
    run([str(py_dir / "python.exe"), "-c", "from cliptool.transcribe import bundled_model; "
         "p = bundled_model('large-v3-turbo'); assert p, 'bundled model not found'; print('Model:', p)"], env=env)
    longest = max(len(str(f.relative_to(STAGE))) for f in STAGE.rglob("*") if f.is_file())
    print(f"Longest path inside the app: {longest} characters (install folders are capped at {MAX_DIR})")
    if longest + MAX_DIR > 250:
        raise SystemExit("Paths too long for Windows MAX_PATH; shorten them or lower MAX_DIR.")


def compile_installer(split: bool = False) -> Path:
    step("Inno Setup")
    iscc = next((p for p in ISCC_PLACES if p.is_file()), None) or shutil.which("ISCC")
    if not iscc:
        raise SystemExit("Inno Setup 6 (ISCC.exe) not found.")
    DIST.mkdir(exist_ok=True)
    for old in DIST.glob(f"OddlyCleverEditor-setup-{__version__}*"):
        old.unlink()  # our own earlier build, so a single-file build never leaves stale .bin parts
    defines = [f"/DAppVersion={__version__}", f"/DMaxDir={MAX_DIR}"] + (["/DSplit"] if split else [])
    run([str(iscc), *defines, str(HERE / "cliptool.iss")])
    if split:
        too_big = [f.name for f in DIST.glob(f"OddlyCleverEditor-setup-{__version__}*") if f.stat().st_size >= GITHUB_MAX]
        if too_big:
            raise SystemExit(f"Over GitHub's 2 GiB per-file limit: {too_big}")
    return DIST / f"OddlyCleverEditor-setup-{__version__}.exe"


def main() -> None:
    split = "--split" in sys.argv[1:]  # GitHub Releases: setup.exe + .bin parts under 2 GiB each
    if STAGE.exists():
        shutil.rmtree(STAGE)  # our own build output from a previous run
    STAGE.mkdir(parents=True)
    py_dir = stage_python()
    stage_packages(py_dir)
    stage_ffmpeg()
    stage_model()
    stage_docs()
    smoke_test(py_dir)
    out = compile_installer(split)
    size = sum(f.stat().st_size for f in DIST.glob(f"OddlyCleverEditor-setup-{__version__}*"))
    print(f"\nDone: {out} ({size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
