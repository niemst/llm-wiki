"""A long-lived MCP server keeps the code it started with. Once the code tree
changes (mtime bump or file deletion) every later tools/call carries a reload
note. The on-disk check runs at most once per 60 s.

Input-only: the code tree is a tmp dir, the clock is faked, the tool is a stub."""

from __future__ import annotations

import os

import pytest

from llmwiki.mcp import server

INTERVAL = server.STALE_CODE_CHECK_INTERVAL_S


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def code_tree(monkeypatch, tmp_path, clock):
    module = tmp_path / "module.py"
    other = tmp_path / "other.py"
    for path in (module, other):
        path.write_text("x = 1\n", encoding="utf-8")
        os.utime(path, (1_000_000, 1_000_000))
    monkeypatch.setattr(server, "CODE_ROOT", tmp_path)
    monkeypatch.setitem(server.TOOL_IMPLS, "wiki_lint", lambda args: server._ok("lint ok"))
    server._init_code_watch()
    return module, other


def _call_texts() -> list[str]:
    result = server.handle_tools_call({"name": "wiki_lint", "arguments": {}})
    return [item["text"] for item in result["content"]]


def test_no_note_without_code_change(code_tree, clock):
    clock[0] += INTERVAL
    assert _call_texts() == ["lint ok"]


def test_mtime_bump_gives_note_on_every_later_call(code_tree, clock):
    module, _ = code_tree
    os.utime(module, (2_000_000, 2_000_000))
    clock[0] += INTERVAL
    assert _call_texts() == ["lint ok", server.STALE_CODE_NOTE]
    assert _call_texts() == ["lint ok", server.STALE_CODE_NOTE]
    clock[0] += INTERVAL
    assert _call_texts() == ["lint ok", server.STALE_CODE_NOTE]


def test_file_deletion_gives_note(code_tree, clock):
    _, other = code_tree
    other.unlink()
    clock[0] += INTERVAL
    assert _call_texts() == ["lint ok", server.STALE_CODE_NOTE]


def test_throttle_skips_disk_check_inside_interval(code_tree, clock):
    module, _ = code_tree
    clock[0] += INTERVAL
    assert _call_texts() == ["lint ok"]
    os.utime(module, (2_000_000, 2_000_000))
    clock[0] += INTERVAL - 1
    assert _call_texts() == ["lint ok"]
    clock[0] += 1
    assert _call_texts() == ["lint ok", server.STALE_CODE_NOTE]
