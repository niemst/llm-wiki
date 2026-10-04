"""tool_wiki_sync must not report a timeout for a child that already exited.

The converter doesn't flush stdout, so output can arrive in one block
after the 120s deadline check even though the child finished normally.
When ``proc.poll()`` is not ``None`` at the deadline, the fix drains the
rest of stdout and returns the normal result instead of killing a
finished process and reporting "sync timed out after 120s".
"""

from __future__ import annotations

from llmwiki.mcp import server


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)

    def __iter__(self) -> "_FakeStdout":
        return self

    def __next__(self) -> str:
        return next(self._lines)


class _FakeFinishedProc:
    """A child that exited before the MCP deadline trips, but whose
    buffered output only reaches us after the deadline check."""

    def __init__(self, lines: list[str]) -> None:
        self.stdout = _FakeStdout(lines)

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        raise AssertionError("a finished child must not be terminated")

    def communicate(self, timeout: float | None = None):
        raise AssertionError("a finished child must not go through communicate")


def test_false_timeout_drains_finished_child(monkeypatch) -> None:
    fake_proc = _FakeFinishedProc(["summary: 1 converted, 0 errors\n"])
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **kw: fake_proc)

    # First time.time() call sets the deadline; the second (checked right
    # after the only buffered line arrives) is already past it.
    times = iter([1000.0, 1200.0])
    monkeypatch.setattr(server.time, "time", lambda: next(times, 1200.0))

    result = server.tool_wiki_sync({"dry_run": True})

    assert result["isError"] is False
    assert "timed out" not in result["content"][0]["text"]
    assert "converted" in result["content"][0]["text"]
