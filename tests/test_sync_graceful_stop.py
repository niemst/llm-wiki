"""convert_all stops at a source boundary on SIGTERM (#wiki-sync-errors).

A real SIGKILL mid-run skips the `finally: _persist_run_state(...)` in
convert_all, so raw files written in that run have no state key and
`_raw_write_guard` / `_is_same_session_refresh` refuse them forever after.
This fix installs a SIGTERM handler that only sets a flag; the loop in
`_convert_all_with_state` checks it at the top of each per-source
iteration and stops there, where the write and the state update for the
just-finished source are already paired, so the existing `finally` saves
a consistent state.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from llmwiki import convert


def _seed_jsonl(project_dir: Path, name: str, iso_ts: str) -> Path:
    """Drop a minimal Claude-Code-shaped jsonl under ``project_dir``."""
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{name}.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {
                    "type": "user",
                    "sessionId": f"{name}-uuid",
                    "slug": name,
                    "timestamp": iso_ts,
                    "cwd": "/tmp",
                    "gitBranch": "main",
                    "message": {"role": "user", "content": "hi"},
                },
                {
                    "type": "assistant",
                    "sessionId": f"{name}-uuid",
                    "timestamp": iso_ts,
                    "message": {"role": "assistant", "content": "hello"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    from llmwiki.adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project_dir = tmp_path / ".claude" / "projects" / "demo-proj"
    monkeypatch.setattr(
        ClaudeCodeAdapter, "session_store_path", project_dir.parent,
    )
    config_file = tmp_path / "sessions_config.json"
    config_file.write_text("{}", encoding="utf-8")
    sources = [
        _seed_jsonl(project_dir, f"session-{i}", f"2026-04-26T0{i}:00:00Z")
        for i in range(3)
    ]
    return {
        "sources": sources,
        "out_dir": tmp_path / "raw" / "sessions",
        "state_file": tmp_path / ".llmwiki-sync-state.json",
        "config_file": config_file,
        "ignore_file": tmp_path / ".llmwiki-ignore",
    }


def test_sigterm_stops_at_source_boundary_with_state_saved(
    fake_repo: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(convert, "_stop_requested", False)
    original_render = convert.render_session_markdown
    calls = {"n": 0}

    def wrapped_render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            # Handler must already be installed by convert_all.
            assert signal.getsignal(signal.SIGTERM) is convert._handle_sigterm
            convert._handle_sigterm(signal.SIGTERM, None)
        return result

    monkeypatch.setattr(convert, "render_session_markdown", wrapped_render)

    rc = convert.convert_all(
        adapters=["claude_code"],
        out_dir=fake_repo["out_dir"],
        state_file=fake_repo["state_file"],
        config_file=fake_repo["config_file"],
        ignore_file=fake_repo["ignore_file"],
        include_current=True,
    )

    assert rc == 0
    outs = sorted(fake_repo["out_dir"].rglob("*.md"))
    assert len(outs) == 1, f"expected exactly 1 file, got {[p.name for p in outs]}"

    saved = json.loads(fake_repo["state_file"].read_text())
    written_key = convert._portable_state_key("claude_code", fake_repo["sources"][0])
    assert written_key in saved
    for unwritten_source in fake_repo["sources"][1:]:
        key = convert._portable_state_key("claude_code", unwritten_source)
        assert key not in saved

    out = capsys.readouterr().out
    assert "stopped early" in out
    assert signal.getsignal(signal.SIGTERM) is not convert._handle_sigterm
