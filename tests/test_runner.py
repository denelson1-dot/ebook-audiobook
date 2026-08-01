"""The background worker's lifecycle — specifically, that it never loses a task.

The worker thread exits when it goes idle and is re-spawned on the next submit.
That is cheap and correct only if the hand-off is airtight: submit() enqueues
*before* it looks at the thread, so a thread deciding to exit at that moment
could be seen as alive by a submit whose work is already in the queue. The task
would then sit there forever, with the job page showing a stage that never
advances and a Stop button that does nothing.
"""

from __future__ import annotations

import queue
import threading
import time

from ebook_audiobook.web.runner import Runner, _Task


def _collecting_runner(idle_timeout: float = 1.0):
    """A Runner whose tasks just record themselves instead of rendering."""
    r = Runner()
    r.IDLE_TIMEOUT = idle_timeout
    done: list[str] = []
    lock = threading.Lock()

    def fake_run(task: _Task) -> None:
        with lock:
            done.append(task.job_id)

    r._run = fake_run
    return r, done


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_a_submitted_task_runs():
    r, done = _collecting_runner()
    r.submit("job-1", "render")
    assert _wait_for(lambda: done == ["job-1"])


def test_no_task_is_lost_when_a_submit_lands_as_the_worker_exits():
    """The race this whole design hinges on, forced rather than waited for.

    The dangerous interleaving is narrow — the worker's get() must time out in
    the instant between submit() enqueueing and submit() deciding the thread is
    still alive — so it is reproduced exactly instead of hoped for: submit() is
    called from inside get(), just before it reports the queue empty. Note that
    the submit runs on the worker thread itself, so its _ensure_thread() sees a
    live thread and declines to spawn a replacement, which is precisely the
    situation that used to strand the task.
    """
    r, done = _collecting_runner(idle_timeout=0.01)
    real_get = r._q.get
    raced = threading.Event()

    def get_that_races(*args, **kwargs):
        try:
            return real_get(*args, **kwargs)
        except queue.Empty:
            if not raced.is_set():
                raced.set()
                r.submit("late-arrival", "render")
            raise

    r._q.get = get_that_races
    r._ensure_thread()

    assert _wait_for(lambda: raced.is_set()), "the race was never triggered"
    assert _wait_for(lambda: done == ["late-arrival"]), (
        "a task queued as the idle worker exited was never run — the job would "
        "sit in the UI forever with a Stop button that does nothing"
    )


def test_a_burst_of_submits_all_run():
    """Ordinary throughput, with the worker constantly reaching its idle timeout."""
    r, done = _collecting_runner(idle_timeout=0.001)
    total = 200
    for i in range(total):
        r.submit(f"job-{i}", "render")
        if i % 5 == 0:
            time.sleep(0.002)  # let the worker hit its idle timeout mid-stream
    assert _wait_for(lambda: len(done) == total), f"only {len(done)}/{total} ran"
    assert sorted(done) == sorted(f"job-{i}" for i in range(total))


def test_the_thread_exits_when_idle_and_restarts_on_demand():
    """Idling must actually release the thread, or this is just a leak."""
    r, done = _collecting_runner(idle_timeout=0.05)
    r.submit("first", "render")
    assert _wait_for(lambda: done == ["first"])
    assert _wait_for(lambda: r._thread is None), "idle worker thread never exited"

    r.submit("second", "render")
    assert _wait_for(lambda: done == ["first", "second"])


def test_a_task_that_raises_does_not_kill_the_worker():
    """One bad job must not silently stop every later one from running."""
    r, done = _collecting_runner()
    calls: list[str] = []

    def boom(task: _Task) -> None:
        calls.append(task.job_id)
        if task.job_id == "bad":
            raise RuntimeError("engine exploded")

    r._run = boom
    r.submit("bad", "render")
    r.submit("good", "render")
    assert _wait_for(lambda: calls == ["bad", "good"])
    # Bookkeeping is settled in _loop's finally, just after _run returns, so
    # wait for it rather than sampling the instant the task's body finished.
    assert _wait_for(lambda: not r.is_busy())


def test_busy_covers_queued_work_not_just_running_work():
    """The UI disables delete/cleanup on `busy`; a queued job must count."""
    r, _done = _collecting_runner()
    release = threading.Event()

    def blocking(task: _Task) -> None:
        release.wait(timeout=5)

    r._run = blocking
    r.submit("slow", "render")
    assert _wait_for(lambda: r.is_busy("slow"))
    assert r.is_busy()
    assert not r.is_busy("some-other-job")
    release.set()
    assert _wait_for(lambda: not r.is_busy())


def test_cancel_is_cleared_when_the_task_finishes():
    """A stale cancel flag would abort the *next* render of the same job."""
    r, done = _collecting_runner()
    r.submit("job-1", "render")
    assert _wait_for(lambda: done == ["job-1"])
    r.cancel("job-1")
    r.submit("job-1", "render")  # new work supersedes the stop
    assert _wait_for(lambda: done == ["job-1", "job-1"])
    assert not r._cancel_requested("job-1")
