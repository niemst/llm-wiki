"""A second site build must not delete files under a running one (#wiki-sync-errors)."""

from __future__ import annotations

import fcntl

from llmwiki.build import build_site


def test_second_build_skips_while_first_holds_the_lock(tmp_path, capsys):
    out_dir = tmp_path / "site"
    out_dir.mkdir()
    sentinel = out_dir / "sessions" / "page.html"
    sentinel.parent.mkdir()
    sentinel.write_text("rendered by the running build")
    lock_path = tmp_path / "site.build.lock"

    with lock_path.open("a") as running_build:
        fcntl.flock(running_build, fcntl.LOCK_EX)
        assert build_site(out_dir=out_dir) == 0

    assert sentinel.read_text() == "rendered by the running build"
    assert "site build already running" in capsys.readouterr().out
