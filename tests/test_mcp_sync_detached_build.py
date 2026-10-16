"""The wiki_sync shim spawns `llmwiki mcp-sync`; that subcommand converts only
and starts the full site build detached after a good live sync.

Input-only: subprocess.Popen and the converter are faked, nothing runs."""

from __future__ import annotations

import pytest

from llmwiki import cli
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
def popen(monkeypatch, tmp_path):
    recorder = _PopenRecorder()
    monkeypatch.setattr(server.subprocess, "Popen", recorder)
    monkeypatch.setattr(cli, "SITE_BUILD_LOG", tmp_path / "site.build.log")
    monkeypatch.setattr(cli, "_load_schedule_config", lambda: {"build": "on-sync", "lint": "manual"})
    monkeypatch.setattr("llmwiki.build.build_site", _no_inline_build)
    return recorder


def _no_inline_build(*args, **kwargs):
    raise AssertionError("the MCP sync path must not build the site inline")


class _ConvertStub:
    def __init__(self, rc: int) -> None:
        self.rc = rc
        self.dry_run_seen: list[bool] = []

    def __call__(self, **kwargs) -> int:
        self.dry_run_seen.append(kwargs["dry_run"])
        return self.rc


@pytest.fixture
def convert(monkeypatch):
    stub = _ConvertStub(rc=0)
    monkeypatch.setattr("llmwiki.convert.convert_all", stub)
    return stub


def test_shim_dry_run_command(popen):
    server.tool_wiki_sync({"dry_run": True})
    assert popen.started[0][0][-2:] == ["mcp-sync", "--dry-run"]


def test_shim_confirm_command(popen):
    server.tool_wiki_sync({"dry_run": False, "confirm": True})
    assert popen.started[0][0][-1] == "mcp-sync"


def test_shim_unconfirmed_live_request_downgrades_to_dry_run(popen):
    server.tool_wiki_sync({"dry_run": False})
    assert popen.started[0][0][-2:] == ["mcp-sync", "--dry-run"]


def test_live_sync_converts_then_starts_detached_build(popen, convert):
    assert cli.main(["mcp-sync"]) == 0
    assert convert.dry_run_seen == [False]
    (build_cmd, build_kwargs), = popen.started
    assert build_cmd[-2:] == ["build", "--seed-project-stubs"]
    assert build_kwargs["start_new_session"] is True


def test_dry_run_starts_no_build(popen, convert):
    assert cli.main(["mcp-sync", "--dry-run"]) == 0
    assert convert.dry_run_seen == [True]
    assert popen.started == []


def test_failed_sync_starts_no_build(popen, convert):
    convert.rc = 1
    cli.main(["mcp-sync"])
    assert popen.started == []


def test_manual_build_schedule_starts_no_build(popen, convert, monkeypatch):
    monkeypatch.setattr(cli, "_load_schedule_config", lambda: {"build": "manual", "lint": "manual"})
    cli.main(["mcp-sync"])
    assert popen.started == []


def test_oversized_build_log_is_truncated(popen, convert, monkeypatch):
    monkeypatch.setattr(cli, "SITE_BUILD_LOG_MAX_BYTES", 4)
    cli.SITE_BUILD_LOG.write_bytes(b"old build output")
    cli.main(["mcp-sync"])
    assert cli.SITE_BUILD_LOG.read_bytes() == b""
