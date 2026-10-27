"""Jobs any AI can do for cliptool, beyond judging clips: write hook titles for picked clips, and
find words Whisper probably got wrong. Each task is a prompt out and a JSON array back, so the
same task works pasted into a chat, run by an agent from files, or sent to an AI server."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .chunking import clean_words
from .transcript import Word

TASKS = ("judge", "titles", "fixes")
# (prompt file, reply file) in the project folder, for agents that work from disk
FILES = {"judge": ("judge.md", "ranking.json"), "titles": ("ai_titles.md", "ai_titles.json"),
         "fixes": ("ai_fixes.md", "ai_fixes.json")}
MAX_TITLE = 60
MAX_FIXES = 40
_WORD = re.compile(r"[\w'’-]+")


@dataclass(frozen=True)
class ClipText:
    id: int
    start: float
    end: float
    text: str


def clip_texts(words: tuple[Word, ...], clips: list[dict]) -> list[ClipText]:
    """The spoken text of each clip the UI sent ({id, start, end}), validated."""
    out = []
    for c in clips[:30]:
        try:
            cid, start, end = int(c["id"]), float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each clip needs a numeric id, start and end.") from None
        if not 0 <= start < end:
            raise ValueError(f"Clip {cid} has a bad time range.")
        text = " ".join(w.text for w in clean_words(words) if start - 0.05 <= w.start and w.end <= end + 0.05)
        out.append(ClipText(cid, start, end, text))
    if not out:
        raise ValueError("Tick at least one clip first.")
    return out


def titles_prompt(video_name: str, clips: list[ClipText]) -> str:
    blocks = "\n\n".join(f"### Clip {c.id} ({c.end - c.start:.0f} s)\n\n{c.text}" for c in clips)
    return f"""# Write hook titles for short clips

These clips were cut from "{video_name}" to post as YouTube Shorts, Reels and TikToks. Each title
is shown big at the top of the clip for its first seconds, so it has to stop someone scrolling.

For each clip write ONE title that:
- is at most 6 words and no more than {MAX_TITLE // 2} characters (never over {MAX_TITLE});
- makes the viewer curious about what happens, using a concrete detail from the clip;
- is true to the clip (no clickbait the clip doesn't deliver);
- has no emojis, hashtags or quotation marks.

Reply with **only** a JSON array, one entry per clip, saved as `ai_titles.json` if you work from files:

```json
[{{"id": 7, "title": "Drunk for a penny"}}]
```

## Clips

{blocks}
"""


def parse_titles(entries: object, ids: set[int]) -> list[dict]:
    if not isinstance(entries, list):
        raise ValueError("The reply must be a JSON array of {id, title}.")
    out, seen = [], set()
    for e in entries:
        if not isinstance(e, dict) or isinstance(e.get("id"), bool):
            continue
        try:
            cid = int(e.get("id"))
        except (TypeError, ValueError):
            continue
        title = " ".join(str(e.get("title") or "").split()).strip("\"'“”")[:MAX_TITLE].strip()
        if cid in ids and title and cid not in seen:
            seen.add(cid)
            out.append({"id": cid, "title": title})
    if not out:
        raise ValueError("The reply has no titles for the ticked clips.")
    return out


def transcript_text(words: tuple[Word, ...]) -> str:
    """The transcript as readable lines with a timestamp every sentence or so."""
    lines, line, start = [], [], None
    for w in clean_words(words):  # "70" + ",000" reads as "70,000"
        if start is None:
            start = w.start
        line.append(w.text)
        if w.text.rstrip("\"'”’)").endswith((".", "?", "!")) or len(line) >= 30:
            m, s = divmod(int(start), 60)
            lines.append(f"[{m}:{s:02d}] {' '.join(line)}")
            line, start = [], None
    if line:
        m, s = divmod(int(start or 0), 60)
        lines.append(f"[{m}:{s:02d}] {' '.join(line)}")
    return "\n".join(lines)


def fixes_prompt(video_name: str, words: tuple[Word, ...]) -> str:
    return f"""# Find transcription mistakes

Below is an automatic (Whisper) transcript of "{video_name}". Find words it probably got wrong:
misspelled names of people and places, technical terms, and sound-alike words that don't fit.

Rules:
- Only confident fixes. Leave anything that is merely unusual but plausible.
- `find` must be ONE word exactly as it appears in the transcript (case-sensitive, no spaces,
  no punctuation). `replace` is the corrected spelling.
- Every occurrence of `find` will be replaced, so don't list a word that is right in some places.
- At most {MAX_FIXES} fixes, most important first.

Reply with **only** a JSON array, saved as `ai_fixes.json` if you work from files:

```json
[{{"find": "Callan", "replace": "Callum", "why": "the host's name, spelled Callum elsewhere"}}]
```

Reply with `[]` if you find nothing.

## Transcript

{transcript_text(words)}
"""


def parse_fixes(entries: object, words: tuple[Word, ...]) -> list[dict]:
    """Keep fixes whose `find` is a whole word that really occurs; count the occurrences."""
    if not isinstance(entries, list):
        raise ValueError("The reply must be a JSON array of {find, replace, why}.")
    counts = Counter(tok for w in words for tok in _WORD.findall(w.text))
    out, seen = [], set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        find = str(e.get("find") or "").strip()
        repl = " ".join(str(e.get("replace") or "").split())
        if (not find or not repl or find == repl or find in seen or len(find) > 60 or len(repl) > 80
                or not _WORD.fullmatch(find) or "=" in find or "=" in repl or not counts.get(find)):
            continue
        seen.add(find)
        out.append({"find": find, "replace": repl, "why": " ".join(str(e.get("why") or "").split())[:200],
                    "count": counts[find]})
        if len(out) >= MAX_FIXES:
            break
    return out
