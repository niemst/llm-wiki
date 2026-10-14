"""A live wiki_sync converts only, inside the 120s MCP budget. The full site
build reads every raw session, so it starts detached after a good sync.

Input-only: subprocess.Popen is faked, nothing runs."""

from __future__ import annotations

import pytest

from llmwiki import config_schedule
from llmwiki.mcp import server


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.stdout = iter(["summary: 1 converted, 0 errors\n"])
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class _PopenRecorder:
    def __init__(self) -> None:
        self.started: list[tuple[list[str], dict]] = []
        self.returncode = 0

    def __call__(self, cmd: list[str], **kwargs) -> _FakeProc:
        self.started.append((cmd, kwargs))
        return _FakeProc(self.returncode)


@pytest.fixture
def calls(monkeypatch, tmp_path):
    recorder = _PopenRecorder()
    monkeypatch.setattr(server.subprocess, "Popen", recorder)
    monkeypatch.setattr(server, "SITE_BUILD_LOG", tmp_path / "site.build.log")
    monkeypatch.setattr(config_schedule, "load_schedule_config", lambda: {"build": "on-sync", "lint": "manual"})
    return recorder.started, recorder


def _live_sync() -> dict:
    return server.tool_wiki_sync({"dry_run": False, "confirm": True})


def test_live_sync_skips_inline_build_and_starts_detached_build(calls):
    started, _ = calls
    result = _live_sync()
    assert result["isError"] is False
    sync_cmd, build_cmd = started[0][0], started[1][0]
    assert sync_cmd[-2:] == ["sync", "--no-auto-build"]
    assert build_cmd[-2:] == ["build", "--seed-project-stubs"]
    assert started[1][1]["start_new_session"] is True
    assert started[1][1]["env"]["PYTHONUNBUFFERED"] == "1"
    assert "site build started in background" in result["content"][0]["text"]


def test_oversized_build_log_is_truncated(calls, monkeypatch):
    monkeypatch.setattr(server, "SITE_BUILD_LOG_MAX_BYTES", 4)
    server.SITE_BUILD_LOG.write_bytes(b"old build output")
    _live_sync()
    assert server.SITE_BUILD_LOG.read_bytes() == b""


def test_dry_run_starts_no_build(calls):
    started, _ = calls
    server.tool_wiki_sync({"dry_run": True})
    assert [cmd[-1] for cmd, _ in started] == ["--dry-run"]


def test_failed_sync_starts_no_build(calls):
    started, fake_popen = calls
    fake_popen.returncode = 1
    _live_sync()
    assert len(started) == 1


def test_manual_build_schedule_starts_no_build(calls, monkeypatch):
    started, _ = calls
    monkeypatch.setattr(config_schedule, "load_schedule_config", lambda: {"build": "manual", "lint": "manual"})
    _live_sync()
    assert len(started) == 1
