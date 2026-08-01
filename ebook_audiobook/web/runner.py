"""Single background worker for the web UI.

One thread, one queue, one job at a time — the same "single worker owns the GPU
model" guarantee the CLI gives, just driven by HTTP requests instead of a
blocking call. Long renders survive because state is persisted to disk each
segment; the thread just drives ``worker.render_job``.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from dataclasses import dataclass, field

from .. import worker
from ..jobs.store import JobStore


def _terminal_progress(job_id: str):
    """A throttled progress printer so the console running the server shows the
    render is alive (segments completing) instead of sitting silent after the
    'loading TTS model' line."""
    last = [0.0]

    def cb(state) -> None:
        now = time.monotonic()
        if now - last[0] < 3.0:
            return
        last[0] = now
        n, m = state.rendered_segments or 0, state.total_segments or 0
        pct = f" ({round(100 * n / m)}%)" if m else ""
        print(f"[{job_id[:8]}] {state.stage}: {n}/{m}{pct}", file=sys.stderr, flush=True)

    return cb


@dataclass
class _Task:
    job_id: str
    kind: str  # "extract" | "preview" | "render"
    kwargs: dict = field(default_factory=dict)


class Runner:
    def __init__(self):
        self._q: "queue.Queue[_Task]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cancel: set[str] = set()  # job_ids for which a stop was requested
        self._pending: dict[str, int] = {}  # job_id -> queued+running task count
        self.current: str | None = None  # "<job_id>:<kind>" while running

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                task = self._q.get(timeout=1.0)
            except queue.Empty:
                return  # idle: let the thread exit; re-spawned on next submit
            self.current = f"{task.job_id}:{task.kind}"
            try:
                self._run(task)
            except Exception:  # noqa: BLE001 - error is recorded on job state
                pass
            finally:
                self.current = None
                with self._lock:
                    self._cancel.discard(task.job_id)
                    n = self._pending.get(task.job_id, 1) - 1
                    if n <= 0:
                        self._pending.pop(task.job_id, None)
                    else:
                        self._pending[task.job_id] = n
                self._q.task_done()

    def _run(self, task: _Task) -> None:
        def cancelled() -> bool:
            return self._cancel_requested(task.job_id)

        if task.kind == "extract":
            worker.extract_job(task.job_id)
        elif task.kind == "preview":
            worker.render_job(
                task.job_id,
                preview_max_seconds=task.kwargs.get("seconds", 30),
                preview_chapter_id=task.kwargs.get("chapter_id"),
                should_cancel=cancelled,
                progress=_terminal_progress(task.job_id),
            )
        elif task.kind == "render":
            worker.render_job(
                task.job_id,
                output_dir=task.kwargs.get("output_dir"),
                output_mode=task.kwargs.get("output_mode"),
                should_cancel=cancelled,
                progress=_terminal_progress(task.job_id),
            )
        elif task.kind == "voice_test":
            worker.render_voice_sample(task.kwargs["voice_id"])

    def submit(self, job_id: str, kind: str, **kwargs) -> None:
        with self._lock:
            self._cancel.discard(job_id)  # new work supersedes a prior stop
            self._pending[job_id] = self._pending.get(job_id, 0) + 1
        self._q.put(_Task(job_id=job_id, kind=kind, kwargs=kwargs))
        self._ensure_thread()

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancel.add(job_id)

    def _cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel

    def is_busy(self, job_id: str | None = None) -> bool:
        """Busy if a task is running OR queued (so a just-submitted job counts)."""
        with self._lock:
            if job_id is None:
                return self.current is not None or bool(self._pending)
            return (self.current or "").startswith(f"{job_id}:") or self._pending.get(job_id, 0) > 0


runner = Runner()
