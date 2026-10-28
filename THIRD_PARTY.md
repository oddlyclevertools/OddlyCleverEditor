# Third-party components and licences

OddlyCleverEditor's own code is MIT (see `LICENSE`) since 28 Oct 2026; versions up to 0.4.0 were GPL-3.0-or-later. Checked 27 Oct 2026; re-checked for v0.4 the same day.

## Bundled in the source folder

| Component | Files | Licence |
|---|---|---|
| Montserrat (Julieta Ulanovsky et al.) | `cliptool/fonts/Montserrat-*.ttf` | SIL OFL 1.1, `cliptool/fonts/OFL-Montserrat.txt` |
| Anton (Vernon Adams et al.) | `cliptool/fonts/Anton-Regular.ttf` | SIL OFL 1.1, `cliptool/fonts/OFL-Anton.txt` |
| Bangers (Vernon Adams) | `cliptool/fonts/Bangers-Regular.ttf` | SIL OFL 1.1, `cliptool/fonts/OFL-Bangers.txt` |
| Bebas Neue (Dharma Type) | `cliptool/fonts/BebasNeue-Regular.ttf` | SIL OFL 1.1, `cliptool/fonts/OFL-BebasNeue.txt` |
| Poppins Black and ExtraBold (Indian Type Foundry) | `cliptool/fonts/Poppins-*.ttf` | SIL OFL 1.1, `cliptool/fonts/OFL-Poppins.txt` |
| Archivo Black (Omnibus-Type) | `cliptool/fonts/ArchivoBlack-Regular.ttf` | SIL OFL 1.1, `cliptool/fonts/OFL-ArchivoBlack.txt` |
| Permanent Marker (Font Diner) | `cliptool/fonts/PermanentMarker-Regular.ttf` | Apache-2.0, `cliptool/fonts/LICENSE-PermanentMarker.txt` |
| YuNet face detector, 2023mar (Shiqi Yu, OpenCV Zoo) | `cliptool/models/face_detection_yunet_2023mar.onnx` | MIT, `cliptool/models/LICENSE-YuNet.txt` |

All the fonts came from the google/fonts repository. They are used only to draw captions and the UI, never sold on their own, as OFL 1.1 allows.

## Python packages

`pip install` brings these. The Windows installer bundles the same pinned versions.

| Component | Licence | Notes |
|---|---|---|
| faster-whisper (SYSTRAN) | MIT | Includes the Silero VAD model (MIT) |
| CTranslate2 (OpenNMT) | MIT | Its Windows wheel ships `cudnn64_9.dll` (NVIDIA cuDNN, see below) |
| onnxruntime | MIT | |
| opencv-python-headless (OpenCV 5) | Apache-2.0; the wheel carries LGPL FFmpeg libraries | Used for face detection, histograms and the installer icon. Video goes through ffmpeg |
| numpy | BSD-3-Clause (+ 0BSD, MIT, Zlib, CC0 parts) | |
| tokenizers, huggingface_hub, hf-xet | Apache-2.0 | |
| PyAV (`av`, pulled in by faster-whisper) | BSD-3-Clause; its wheels carry LGPL FFmpeg libraries | OddlyCleverEditor doesn't call it: audio is decoded by ffmpeg instead |
| tqdm | MPL-2.0 AND MIT | |
| Other small deps (anyio, click, colorama, filelock, fsspec, h11, httpx2, httpcore2, idna, packaging, protobuf, PyYAML, typing_extensions, flatbuffers, truststore) | MIT / BSD / Apache-2.0 / PSF | |
| **nvidia-cublas-cu12, nvidia-cuda-nvrtc-cu12** | **NVIDIA CUDA Toolkit EULA** | cuBLAS, cuBLASLt and NVRTC are on the EULA's list of redistributable runtime files (Attachment A) |

## Also in the Windows installer (`installer/build.py`)

| Component | Licence | Notes |
|---|---|---|
| Python 3.12.10 embeddable package (python.org) | PSF License 2.0 | `python/LICENSE.txt` is included |
| ffmpeg and ffprobe 9.0.1, gyan.dev "full" build | GPL-3.0 | Run as separate programs. The build's licence and README (with the exact source commit, FFmpeg `bf1b838f2a`) ship as `ffmpeg/FFMPEG-LICENSE` and `ffmpeg/FFMPEG-README.txt`. Source: https://github.com/FFmpeg/FFmpeg and https://www.gyan.dev/ffmpeg/builds/ |
| Whisper large-v3-turbo weights (OpenAI), CTranslate2 conversion `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | MIT | This is the repo faster-whisper maps `large-v3-turbo` to. Commercial use is allowed |
| NVIDIA cuDNN 9 runtime (`cudnn64_9.dll`, inside the CTranslate2 wheel) | NVIDIA cuDNN Software License Agreement | Redistribution of the runtime library inside an application is allowed. The installer only redistributes the unchanged wheel |
| Inno Setup 6.7.3 (build tool, not shipped) | Inno Setup licence | It allows commercial use, and the setup.exe it makes carries no extra terms for users |

The installer is unsigned for now, so Windows SmartScreen will show "unknown publisher".

## AI providers (not bundled)

OddlyCleverEditor bundles no AI model and no provider SDK. The AI panel and `--judge-url` talk to any OpenAI-compatible server with Python's standard `urllib`. The provider list in the UI only fills in public API addresses. The user picks the provider and supplies their own key and model, under that provider's own terms. The transcript text goes to that provider only when the user presses **Ask my AI** (after a confirmation for a server off this PC).

## Still to watch

- **Trademarks.** Never use Descript's, Opus Clip's or Submagic's names, logos or UI text in the product. Comparisons in the README must stay factual.
- **Inno Setup's commercial terms.** The bundled `license.txt` allows any use. The 6.7.3 compiler still prints "Non-commercial use only", matching the newer paid commercial licence on jrsoftware.org. Giving the installer away free is non-commercial, so nothing is needed now. If we ever sell it or build it for a business, buy the commercial licence or switch to WiX/NSIS.
- **Provider names in the AI list.** They are plain-text labels next to public API addresses, with no logos. Keep them that way, and don't imply any partnership.
- **A future cuDNN or cuBLAS version.** Re-read NVIDIA's terms when the CUDA wheels change major version.
