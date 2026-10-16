"""A long-lived MCP server keeps the code it started with. After a source
mtime bump the next tools/call carries a reload note; without a bump it does not.

Input-only: the code tree is a tmp dir and the tool is a stub."""

from __future__ import annotations

import os

import pytest

from llmwiki.mcp import server


@pytest.fixture
def code_tree(monkeypatch, tmp_path):
    source = tmp_path / "module.py"
    source.write_text("x = 1\n", encoding="utf-8")
    os.utime(source, (1_000_000, 1_000_000))
    monkeypatch.setattr(server, "CODE_ROOT", tmp_path)
    monkeypatch.setitem(server.TOOL_IMPLS, "wiki_lint", lambda args: server._ok("lint ok"))
    server._init_code_watch()
    return source


def _call_texts() -> list[str]:
    server._last_stale_check = float("-inf")
    result = server.handle_tools_call({"name": "wiki_lint", "arguments": {}})
    return [item["text"] for item in result["content"]]


def test_no_note_without_source_change(code_tree):
    assert _call_texts() == ["lint ok"]


def test_note_after_source_mtime_bump(code_tree):
    os.utime(code_tree, (2_000_000, 2_000_000))
    assert _call_texts() == ["lint ok", server.STALE_CODE_NOTE]
