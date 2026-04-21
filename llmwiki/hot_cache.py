"""Hot cache refresher — Patch 3.

Rolls a summary of the N most recently ingested sessions into `wiki/hot.md`,
which Pratiyush already scaffolds but doesn't maintain. Hermes and Claude
Code can read `hot.md` on SessionStart for fast "what was I doing" recall
without hitting the full wiki query path.

Usage:
    python3 -m llmwiki.hot_cache                    # refresh from last 10
    python3 -m llmwiki.hot_cache --n 20
    python3 -m llmwiki.hot_cache --dry-run
    python3 -m llmwiki.hot_cache --model claude-haiku-4-5

Output format in wiki/hot.md:

    ---
    title: "Hot Cache"
    type: navigation
    last_updated: YYYY-MM-DD
    auto_maintained: true
    ---

    # Hot Cache

    *Auto-maintained. Last N session summaries, newest first.*

    ## <session-slug-1> — YYYY-MM-DD
    - bullet
    - bullet

    ## <session-slug-2> — YYYY-MM-DD
    ...
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llmwiki import REPO_ROOT
from llmwiki._frontmatter import parse_frontmatter

SOURCES_DIR = REPO_ROOT / "raw" / "sessions"
HOT_MD = REPO_ROOT / "wiki" / "hot.md"

PROMPT = """Summarize this coding/chat session in 3-5 concrete bullets. Focus on: decisions made, problems solved, open questions, named entities (projects/people/tools). Skip pleasantries and meta-commentary.

Output ONLY bullets, one per line, starting with "- ". No preamble, no headers.

Session:
{body}
"""


def _discover_recent(n: int) -> list[Path]:
    """Return the N most recently modified .md files under raw/sessions/."""
    if not SOURCES_DIR.exists():
        return []
    files = [p for p in SOURCES_DIR.rglob("*.md")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:n]


def _llm(prompt: str, model: str | None) -> str:
    cmd = shlex.split(os.environ.get("BRAIN_LLM_CMD", "claude -p"))
    if model or os.environ.get("BRAIN_LLM_MODEL"):
        m = model or os.environ["BRAIN_LLM_MODEL"]
        cmd += ["--model", m]
    try:
        res = subprocess.run(
            cmd, input=prompt, capture_output=True,
            text=True, timeout=120, check=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        msg = getattr(e, "stderr", str(e))
        return f"- (summary unavailable: {msg[:100]})"
    return res.stdout.strip()


def _session_date(meta: dict[str, Any], path: Path) -> str:
    d = meta.get("date") or meta.get("last_updated")
    if d:
        return str(d)
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return ts.strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def refresh(*, n: int = 10, dry_run: bool = False, model: str | None = None, verbose: bool = False) -> int:
    recent = _discover_recent(n)
    if not recent:
        print(f"no sessions under {SOURCES_DIR}", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out: list[str] = [
        "---",
        'title: "Hot Cache"',
        "type: navigation",
        f"last_updated: {today}",
        "auto_maintained: true",
        f"source_count: {len(recent)}",
        "---",
        "",
        "# Hot Cache",
        "",
        f"*Auto-maintained. Last {len(recent)} session summaries, newest first.*",
        "",
    ]

    for path in recent:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        date = _session_date(meta, path)
        slug = path.stem
        if verbose:
            print(f"summarizing {slug} ({date})")
        # Cap body to keep prompt bounded.
        body_capped = body[:12000]
        summary = _llm(PROMPT.format(body=body_capped), model)
        out.append(f"## [[{slug}]] — {date}")
        out.append("")
        for line in summary.splitlines():
            s = line.strip()
            if s.startswith("- "):
                out.append(s)
        out.append("")

    rendered = "\n".join(out).rstrip() + "\n"
    if dry_run:
        print(rendered)
        return 0
    HOT_MD.parent.mkdir(parents=True, exist_ok=True)
    HOT_MD.write_text(rendered, encoding="utf-8")
    print(f"wrote {HOT_MD} ({len(recent)} sessions)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llmwiki-hot-cache")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    return refresh(n=args.n, dry_run=args.dry_run, model=args.model, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
