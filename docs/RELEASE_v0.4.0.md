# OddlyCleverEditor 0.4.0

Free, offline captions and Shorts on your own NVIDIA GPU. No subscription, no credits, no watermark, and your video never leaves your PC.

## Download (Windows 10/11, NVIDIA GPU)

GitHub caps each file at 2 GiB, so the full offline installer comes in parts:

1. Download **all** of these into the same folder:
   - `OddlyCleverEditor-setup-0.4.0.exe` (2.5 MB)
   - `OddlyCleverEditor-setup-0.4.0-1.bin` (1.9 GB)
   - `OddlyCleverEditor-setup-0.4.0-2.bin` (0.5 GB)
2. Run `OddlyCleverEditor-setup-0.4.0.exe`. It finds the `.bin` parts next to it.

The install needs no admin rights and nothing is downloaded during install or first run. It bundles a private Python, ffmpeg and the Whisper large-v3-turbo model, and takes about 3.4 GB of disk.

Windows SmartScreen may say "Windows protected your PC" because the installer isn't code-signed yet. Click **More info**, then **Run anyway**.

## What it does

- **Captions:** word-timed transcripts on the GPU (about 30 s for a 17-minute video on an RTX 5050), with SRT and VTT for uploads and styled captions burned in with NVENC.
- **10 caption styles**, including the new karaoke, boxed, bounce, neon, comic, typewriter and marker styles. Emphasis colours numbers and names.
- **Shorts:** it finds the best moments in a long video and renders them as 9:16 clips that follow faces. Each gets an animated hook title (banner, sticker or outline).
- **A browser UI:** a style gallery that plays each style over your video, a waveform timeline, filmstrips, a phone preview, and a transcript editor.
- **Any AI you like, or none:** pick the best clips, write hook titles, or find transcript mistakes.
  - Use Claude Code or any chat app by copy and paste, or a local model (Ollama, LM Studio, llama.cpp).
  - Or use a cloud API (Anthropic, OpenAI, Gemini, OpenRouter, Groq, Mistral, DeepSeek...).
  - Your API key stays in the page's memory and is never saved.

## Checksums (SHA-256)

```
0c99a1c437135aa3fbb253c51e472e3d0f88adba04349338d4951bb4edf6b557  OddlyCleverEditor-setup-0.4.0-1.bin
298dad37ea76c380e1a2b1427e7a2448d0a565cea6dd62c0754fc079fb5a83b5  OddlyCleverEditor-setup-0.4.0-2.bin
19be135d17ac250c7165969d275ca433bb9c9a755034239a3dd42cb967dae750  OddlyCleverEditor-setup-0.4.0.exe
```

## Licence

GPL-3.0-or-later, with an extra permission for linking NVIDIA's CUDA libraries. The bundled fonts, models and tools are listed with their licences in THIRD_PARTY.md.
