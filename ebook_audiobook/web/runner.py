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

from .. import narration_langs, power, worker
from ..jobs.store import JobStore


def _terminal_progress(job_id: str):
    """A throttled progress printer so the console running the server shows the
    render is alive (segments completing) instead of sitting silent after the
    'loading TTS model' line.

    Returns None when there is no console to print to — launched from the
    application menu or a Start Menu shortcut, stderr is a pipe, the journal, or
    (under pythonw) the null device, and printing there is at best invisible and
    at worst several thousand lines of noise in the system log per render.
    """
    stderr = getattr(sys, "stderr", None)
    try:
        if not (stderr and stderr.isatty()):
            return None
    except (AttributeError, ValueError):
        return None

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
    kind: str  # "extract" | "preview" | "render" | "measure" | "voice_test" | "model_download"
    kwargs: dict = field(default_factory=dict)


class Runner:
    # How long the worker waits for new work before exiting. Not a tuning knob —
    # it's here so the idle-exit race can be stress-tested at a timeout short
    # enough to hit the window thousands of times a second.
    IDLE_TIMEOUT = 1.0

    def __init__(self):
        self._q: "queue.Queue[_Task]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cancel: set[str] = set()  # job_ids for which a stop was requested
        self._pending: dict[str, int] = {}  # job_id -> queued+running task count
        self.current: str | None = None  # "<job_id>:<kind>" while running

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                task = self._q.get(timeout=self.IDLE_TIMEOUT)
            except queue.Empty:
                # Idle — the thread exits and is re-spawned on the next submit.
                # Deciding that under the lock is what makes it safe: submit()
                # enqueues *before* calling _ensure_thread, so a thread that
                # simply returned here could be seen as still alive by a submit
                # that had already queued work, and the task would sit in the
                # queue forever with the UI stuck on "queued" and a Stop button
                # that does nothing. Re-checking the queue while holding the same
                # lock closes that window in both directions.
                with self._lock:
                    if not self._q.empty():
                        continue
                    self._thread = None
                    return
            self.current = f"{task.job_id}:{task.kind}"
            try:
                self._run(task)
            except Exception as e:  # noqa: BLE001 - error is recorded on job state
                # The job page shows the message; the log keeps the traceback,
                # which is the only thing that makes a report actionable.
                from .. import errorlog

                errorlog.record(e, op=task.kind, job_id=task.job_id)
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

            # A quiet render lowers this thread's scheduling priority, and on
            # Linux an unprivileged process can raise its niceness but never
            # lower it again. Retiring the thread is how that gets undone: the
            # next submit starts a fresh one at normal priority, so a single
            # background render can't quietly slow down every job after it.
            if power.thread_is_tainted():
                with self._lock:
                    self._thread = None
                return

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
                power_mode=task.kwargs.get("power_mode"),
                should_cancel=cancelled,
                progress=_terminal_progress(task.job_id),
            )
        elif task.kind == "render":
            worker.render_job(
                task.job_id,
                output_dir=task.kwargs.get("output_dir"),
                output_mode=task.kwargs.get("output_mode"),
                power_mode=task.kwargs.get("power_mode"),
                should_cancel=cancelled,
                progress=_terminal_progress(task.job_id),
            )
        elif task.kind == "measure":
            worker.measure_job(
                task.job_id,
                power_mode=task.kwargs.get("power_mode"),
                should_cancel=cancelled,
                progress=_terminal_progress(task.job_id),
            )
        elif task.kind == "voice_test":
            worker.render_voice_sample(task.kwargs["voice_id"])
        elif task.kind == "model_download":
            # Through the one worker on purpose: a download and a render on the
            # same disk at the same time helps neither, and Quit already knows
            # how to warn about whatever this thread is doing.
            narration_langs.install(task.kwargs["pack"], should_cancel=cancelled)

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

    def current_kind(self) -> str | None:
        """What is running right now — "render", "preview", "extract",
        "voice_test" — or None.

        The quit paths use this to size their warning: interrupting a six-hour
        render deserves a different sentence from interrupting a voice sample
        that will be finished before the user reads it.
        """
        current = self.current
        if not current:
            return None
        _, _, kind = current.partition(":")
        return kind or None


runner = Runner()
