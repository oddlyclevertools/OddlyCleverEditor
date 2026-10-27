"""Projects for the UI: one folder per video in the workspace, holding every output."""
from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from .. import media

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts", ".mp3", ".wav", ".m4a"}
_ID = re.compile(r"^[a-z0-9_]{1,40}-[0-9a-f]{6}$")


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    source: str  # absolute path of the video (inside the project if uploaded)
    uploaded: bool
    created: float


class Store:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, pid: str) -> Path:
        if not _ID.match(pid):
            raise KeyError(pid)
        return self.root / pid

    def get(self, pid: str) -> Project:
        meta = self.folder(pid) / "project.json"
        if not meta.is_file():
            raise KeyError(pid)
        return Project(**json.loads(meta.read_text(encoding="utf-8")))

    def all(self) -> list[Project]:
        out = []
        for meta in self.root.glob("*/project.json"):
            try:
                out.append(Project(**json.loads(meta.read_text(encoding="utf-8"))))
            except (OSError, TypeError, ValueError):
                continue  # skip a damaged folder rather than break the list
        return sorted(out, key=lambda p: -p.created)

    def _new_id(self, name: str) -> str:
        stem = re.sub(r"[^a-z0-9]+", "_", Path(name).stem.lower()).strip("_")[:40] or "video"
        return f"{stem}-{secrets.token_hex(3)}"

    def _save(self, project: Project) -> Project:
        folder = self.folder(project.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "project.json").write_text(json.dumps(asdict(project), indent=1), encoding="utf-8")
        return project

    def from_path(self, path: str) -> Project:
        src = Path(path.strip().strip('"')).expanduser()
        if not src.is_file():
            raise ValueError(f"File not found: {src}")
        if src.suffix.lower() not in VIDEO_EXTS:
            raise ValueError(f"Not a supported video or audio file: {src.name}")
        media.probe(src)  # fails clearly if it has no audio
        project = Project(self._new_id(src.name), src.name, str(src.resolve()), False, time.time())
        return self._save(project)

    def from_upload(self, name: str, body: BinaryIO, length: int) -> Project:
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXTS:
            raise ValueError(f"Not a supported video or audio file: {name}")
        if length <= 0:
            raise ValueError("Empty upload.")
        if shutil.disk_usage(self.root).free < length + 1_000_000_000:
            raise ValueError("Not enough free disk space for this upload (needs its size + 1 GB).")
        pid = self._new_id(name)
        folder = self.folder(pid)
        folder.mkdir(parents=True)
        dest = folder / f"source{ext}"
        remaining = length
        try:
            with dest.open("wb") as f:
                while remaining > 0:
                    chunk = body.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ValueError("Upload was cut off.")
                    f.write(chunk)
                    remaining -= len(chunk)
            media.probe(dest)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)  # only the half-made project we just created
            raise
        return self._save(Project(pid, Path(name).name, str(dest), True, time.time()))

    def file(self, pid: str, rel: str) -> Path:
        """An output file inside the project folder; never anything outside it."""
        folder = self.folder(pid).resolve()
        path = (folder / rel).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise KeyError(rel)
        return path
