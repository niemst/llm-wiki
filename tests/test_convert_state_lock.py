"""convert_all keeps raw/ and the state file consistent (#wiki-sync-errors).

A raw file whose source key never reached the state is refused by
``_raw_write_guard`` on every later sync. Two causes lose that key: a run
that dies before its final save, and two overlapping runs where the later
save overwrites the earlier one.
"""

from __future__ import annotations

import fcntl
import json
import threading
import time
from pathlib import Path

import pytest

from llmwiki import convert


def test_state_is_saved_when_the_run_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"

    def body_that_writes_then_raises(state, counters, **_kwargs):
        state["codex_cli::sessions/a.jsonl"] = 123.0
        raise RuntimeError("killed after the raw write")

    monkeypatch.setattr(convert, "_convert_all_with_state", body_that_writes_then_raises)
    with pytest.raises(RuntimeError):
        convert.convert_all(state_file=state_file)
    saved = json.loads(state_file.read_text())
    assert saved["codex_cli::sessions/a.jsonl"] == 123.0
    assert "_meta" in saved


def test_dry_run_saves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(convert, "_convert_all_with_state", lambda state, counters, **_kwargs: 0)
    assert convert.convert_all(state_file=state_file, dry_run=True) == 0
    assert not state_file.exists()


def test_second_run_waits_for_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"
    started = threading.Event()

    def body(state, counters, **_kwargs):
        started.set()
        return 0

    monkeypatch.setattr(convert, "_convert_all_with_state", body)
    lock_path = state_file.with_name(state_file.name + ".lock")
    with lock_path.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        run = threading.Thread(target=convert.convert_all, kwargs={"state_file": state_file})
        run.start()
        time.sleep(0.3)
        assert not started.is_set(), "a second sync must wait for the running one"
        fcntl.flock(held, fcntl.LOCK_UN)
    run.join(timeout=5)
    assert started.is_set()


def test_save_state_replaces_atomically(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    convert.save_state(state_file, {"k": 1.0})
    assert json.loads(state_file.read_text()) == {"k": 1.0}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
