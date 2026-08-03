"""The diagnostic log: small, private, and machine-readable.

Every test here defends one of those three properties. The log exists to make a
failure reportable hours after the terminal window is gone; it earns its place
only if it cannot grow without bound and cannot leak who you are.
"""

from __future__ import annotations

import json
import os
import time

from ebook_audiobook import errorlog


def _boom(message: str = "kaboom") -> Exception:
    try:
        raise ValueError(message)
    except ValueError as e:
        return e


# --- it records what's needed -------------------------------------------------

def test_records_an_error_with_its_context():
    errorlog.record(_boom(), op="render", job_id="abc123")
    (entry,) = errorlog.entries()
    assert entry["op"] == "render"
    assert entry["job_id"] == "abc123"
    assert entry["error"] == "ValueError"
    assert entry["message"] == "kaboom"
    assert "ValueError: kaboom" in entry["traceback"]


def test_every_line_is_valid_json():
    """An assistant should be able to read this with one json.loads per line."""
    for i in range(5):
        errorlog.record(_boom(f"error {i}"), op="render")
    lines = errorlog.log_path().read_text("utf-8").strip().splitlines()
    assert len(lines) == 5
    for line in lines:
        assert json.loads(line)["error"] == "ValueError"


def test_records_the_environment_a_report_needs():
    errorlog.record(_boom(), op="extract")
    (entry,) = errorlog.entries()
    for key in ("version", "python", "platform", "machine", "device"):
        assert entry.get(key), f"{key} missing — a report without it is unactionable"


def test_extra_context_is_kept():
    errorlog.record(_boom(), op="render", extra={"chapter": 7})
    assert errorlog.entries()[0]["chapter"] == 7


def test_logging_never_raises(monkeypatch):
    """A broken logger must not be what takes down a three-hour render."""
    monkeypatch.setattr(errorlog, "_logger", lambda: (_ for _ in ()).throw(OSError("disk full")))
    errorlog.record(_boom(), op="render")  # must not raise


# --- it stays small -----------------------------------------------------------

def test_a_huge_traceback_is_truncated():
    exc = _boom("x" * 50_000)
    errorlog.record(exc, op="render")
    entry = errorlog.entries()[0]
    assert len(entry["traceback"]) <= errorlog.MAX_TRACEBACK_CHARS + 100
    assert len(entry["message"]) <= 1_000


def test_the_log_rotates_instead_of_growing(monkeypatch):
    monkeypatch.setattr(errorlog, "MAX_BYTES", 2_000)
    errorlog._handlers.clear()
    for i in range(200):
        errorlog.record(_boom(f"error number {i}"), op="render")
    cap = errorlog.MAX_BYTES * (errorlog.BACKUP_COUNT + 1)
    assert errorlog.total_bytes() <= cap * 1.5, "rotation is not bounding the log"
    assert len(list(errorlog.log_dir().glob("errors.log*"))) <= errorlog.BACKUP_COUNT + 1


def test_old_logs_are_pruned():
    errorlog.record(_boom(), op="render")
    path = errorlog.log_path()
    assert path.exists()
    ancient = time.time() - (errorlog.MAX_AGE_DAYS + 1) * 86_400
    os.utime(path, (ancient, ancient))
    assert errorlog.prune() == 1
    assert not path.exists()


def test_recent_logs_are_kept():
    errorlog.record(_boom(), op="render")
    assert errorlog.prune() == 0
    assert errorlog.log_path().exists()


def test_clear_removes_everything():
    errorlog.record(_boom(), op="render")
    assert errorlog.clear() >= 1
    assert errorlog.entries() == []


# --- it lets go of its files --------------------------------------------------
#
# Windows is where this went wrong first — it refuses to unlink an open file, so
# prune() and clear() reported removing nothing while the log stayed put. But
# the leak underneath was never platform-specific, so these check the property
# directly rather than waiting for a Windows runner to notice.

def _open_handlers() -> list:
    return [h for log in errorlog._handlers.values() for h in log.handlers]


def _all_closed(handlers) -> bool:
    """Whether every handler has actually let go of its file.

    Deliberately asks the handler objects themselves rather than checking that
    our ``_handlers`` dict is empty: the original code emptied that dict without
    closing anything, so a test written against it would have passed while the
    file stayed open — which is the entire bug.
    """
    return all(h.stream is None or h.stream.closed for h in handlers)


def test_clear_closes_the_log_handles():
    errorlog.record(_boom(), op="render")
    handlers = _open_handlers()
    assert handlers, "expected the record above to have opened the log"
    errorlog.clear()
    assert _all_closed(handlers)


def test_pruning_an_old_log_closes_its_handle():
    errorlog.record(_boom(), op="render")
    handlers = _open_handlers()
    ancient = time.time() - (errorlog.MAX_AGE_DAYS + 1) * 86_400
    os.utime(errorlog.log_path(), (ancient, ancient))
    errorlog.prune()
    assert _all_closed(handlers)


def test_pruning_nothing_leaves_the_handle_alone():
    """record() prunes on every write. Closing and reopening the file each time
    would turn one append into an open, a write and a close."""
    errorlog.record(_boom(), op="render")
    before = _open_handlers()
    assert errorlog.prune() == 0
    assert _open_handlers() == before


def test_logging_after_a_clear_does_not_double_every_line():
    """The leak that was hiding behind the Windows failure.

    ``logging`` hands back the same logger object for a given name forever, so
    clearing only our own dict left the old handler attached. The next record()
    saw no cached entry, attached a second handler, and from then on every
    failure was written to the file twice.
    """
    errorlog.record(_boom("first"), op="render")
    errorlog.clear()
    errorlog.record(_boom("second"), op="render")

    assert len(_open_handlers()) == 1
    messages = [e["message"] for e in errorlog.entries()]
    assert messages == ["second"], f"expected one entry, got {messages}"


# --- it stays private ---------------------------------------------------------

def test_redacts_the_home_directory():
    home = os.path.expanduser("~")
    assert errorlog.redact(f"{home}/books/secret.epub") == "~/books/secret.epub"


def test_redacts_other_platforms_home_directories():
    """A Linux box reporting a Mac user's path still shouldn't name them."""
    assert "alice" not in errorlog.redact("/Users/alice/Books/thing.epub")
    assert "bob" not in errorlog.redact("/home/bob/Books/thing.epub")
    assert "carol" not in errorlog.redact(r"C:\Users\carol\Books\thing.epub")


def test_the_report_does_not_leak_the_username():
    home = os.path.expanduser("~")
    errorlog.record(_boom(f"could not read {home}/Books/My Diary.epub"), op="extract")
    report = errorlog.issue_report()
    assert home not in report
    assert "~/Books/My Diary.epub" in report


def test_the_report_omits_book_titles():
    """What someone reads is not diagnostic information."""
    errorlog.record(_boom(), op="render", extra={"book": "A Very Private Title"})
    assert "A Very Private Title" not in errorlog.issue_report()


def test_the_report_is_useful_with_no_errors():
    text = errorlog.issue_report()
    assert "No errors have been logged" in text
    assert "Environment" in text


def test_the_report_contains_what_a_maintainer_needs():
    errorlog.record(_boom("the actual failure"), op="render", job_id="job42")
    report = errorlog.issue_report()
    assert "the actual failure" in report
    assert "job42" in report
    assert "ValueError" in report
    assert "Traceback" in report


def test_write_report_writes_a_file(tmp_path):
    errorlog.record(_boom(), op="render")
    dest = errorlog.write_report(tmp_path / "report.md")
    assert dest.read_text("utf-8").startswith("## Environment")
