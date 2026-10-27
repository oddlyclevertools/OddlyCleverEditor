"""One GPU job at a time (transcribe, burn, find, render), with progress the page can poll."""
from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from typing import Any, Callable

log = logging.getLogger("cliptool")

Work = Callable[[Callable[[float, str], None]], Any]


class Busy(Exception):
    """The same kind of job is already queued or running for this project."""


class Cancelled(Exception):
    """Raised inside a job's progress callback once the user presses Cancel."""


@dataclass(frozen=True)
class JobState:
    id: str
    kind: str
    project: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = "Waiting for the GPU..."
    result: Any = None
    error: str = ""
    started: float = field(default_factory=time.time)


class Jobs:
    def __init__(self) -> None:
        self._states: dict[str, JobState] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, Work]] = queue.Queue()
        threading.Thread(target=self._worker, name="cliptool-gpu", daemon=True).start()

    def submit(self, kind: str, project: str, work: Work) -> JobState:
        with self._lock:
            busy = [s for s in self._states.values()
                    if s.project == project and s.kind == kind and s.status in ("queued", "running")]
            if busy:
                raise Busy(f"A {kind} job is already running for this project.")
            state = JobState(secrets.token_hex(6), kind, project)
            self._states[state.id] = state
        self._queue.put((state.id, work))
        return state

    def get(self, job_id: str) -> JobState:
        with self._lock:
            return self._states[job_id]

    def cancel(self, job_id: str) -> JobState:
        with self._lock:
            state = self._states[job_id]
            if state.status == "queued":
                self._states[job_id] = replace(state, status="error", error="Cancelled.")
            elif state.status == "running":
                self._cancel.add(job_id)
            return self._states[job_id]

    def active(self) -> list[JobState]:
        with self._lock:
            return [s for s in self._states.values() if s.status in ("queued", "running")]

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._states[job_id] = replace(self._states[job_id], **changes)

    def _worker(self) -> None:
        while True:
            job_id, work = self._queue.get()
            if self.get(job_id).status != "queued":
                continue  # cancelled while waiting
            self._update(job_id, status="running", message="Starting...")

            def progress(fraction: float, message: str = "", job_id: str = job_id) -> None:
                if job_id in self._cancel:
                    raise Cancelled("Cancelled.")
                changes: dict[str, Any] = {"progress": round(max(0.0, min(1.0, fraction)), 4)}
                if message:
                    changes["message"] = message
                self._update(job_id, **changes)

            try:
                result = work(progress)
                self._update(job_id, status="done", progress=1.0, message="Done", result=result)
            except Cancelled:
                self._update(job_id, status="error", error="Cancelled.")
            except Exception as e:  # report every failure to the page instead of killing the worker
                log.error("Job %s failed: %s\n%s", job_id, e, traceback.format_exc())
                self._update(job_id, status="error", error=f"{type(e).__name__}: {e}" if not isinstance(
                    e, (RuntimeError, ValueError)) else str(e))
            finally:
                with self._lock:
                    self._cancel.discard(job_id)
