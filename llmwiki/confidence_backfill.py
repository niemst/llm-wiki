"""Confidence backfill — Patch 1 of Bartek's llmwiki borrowing set.

Walks every page under wiki/ and writes a `confidence: 0.xx` frontmatter
field using the existing 4-factor formula in `llmwiki.confidence`.

Why: Pratiyush ships the scoring code but never calls it during sync/build,
so `wiki_confidence` returns zero pages. This backfill (and the
`--watch` mode) closes that gap without touching upstream files.

Usage:
    python3 -m llmwiki.confidence_backfill --dry-run
    python3 -m llmwiki.confidence_backfill            # write in place
    python3 -m llmwiki.confidence_backfill --only wiki/entities
    python3 -m llmwiki.confidence_backfill --min-score 0.5  # flag low-score pages

Exit codes:
    0 — success
    1 — unexpected error
    2 — one or more pages scored below `--min-score` (useful in CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llmwiki import REPO_ROOT
from llmwiki._patch_compat import parse_frontmatter
from llmwiki.confidence import compute_confidence

WIKI_DIR = REPO_ROOT / "wiki"

# Pages exempted from confidence scoring (nav hubs, system files).
EXEMPT = {
    "index.md", "overview.md", "log.md", "hints.md",
    "hot.md", "MEMORY.md", "SOUL.md", "CRITICAL_FACTS.md",
    "dashboard.md",
}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def _count_inbound_links(target_slug: str, all_bodies: dict[str, str]) -> int:
    """Count how many other pages wikilink to `target_slug`."""
    n = 0
    for slug, body in all_bodies.items():
        if slug == target_slug:
            continue
        for m in WIKILINK_RE.finditer(body):
            if m.group(1).strip().lower() == target_slug.lower():
                n += 1
                break  # one per page
    return n


def _infer_source_quality(meta: dict[str, Any], body: str) -> list[str]:
    """Map a page's metadata to one or more source_quality labels."""
    page_type = (meta.get("type") or "").lower()
    tags = [t.lower() for t in (meta.get("tags") or [])]

    if page_type == "source":
        return ["session_transcript"]
    if "official" in tags or "documentation" in tags:
        return ["documentation"]
    if "paper" in tags or "peer_reviewed" in tags:
        return ["peer_reviewed"]
    if page_type in ("entity", "concept") and meta.get("sources"):
        return ["session_transcript"] * len(meta["sources"])
    if "blog" in tags:
        return ["blog"]
    return ["unknown"]


def _source_count(meta: dict[str, Any]) -> int:
    sources = meta.get("sources") or []
    if isinstance(sources, str):
        return 1
    return max(1, len(sources))


def _last_updated(meta: dict[str, Any], path: Path) -> str | None:
    lu = meta.get("last_updated")
    if lu:
        return str(lu)
    # Fall back to file mtime.
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return ts.strftime("%Y-%m-%d")
    except OSError:
        return None


def _write_frontmatter(path: Path, meta: dict[str, Any], body: str) -> None:
    """Serialize frontmatter + body back to disk (preserves key order)."""
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            rendered = "[" + ", ".join(str(x) for x in v) + "]"
        elif isinstance(v, bool):
            rendered = "true" if v else "false"
        elif v is None:
            rendered = ""
        else:
            rendered = str(v)
        lines.append(f"{k}: {rendered}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    path.write_text(text, encoding="utf-8")


def _slug(path: Path) -> str:
    return path.stem


def backfill(
    *,
    dry_run: bool = False,
    only: Path | None = None,
    min_score: float | None = None,
    verbose: bool = False,
) -> int:
    root = only or WIKI_DIR
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1

    md_paths = [p for p in root.rglob("*.md") if p.name not in EXEMPT]
    if not md_paths:
        print("no pages found", file=sys.stderr)
        return 1

    # Pre-load bodies for inbound-link counting.
    all_bodies: dict[str, str] = {}
    parsed: dict[Path, tuple[dict, str]] = {}
    for p in md_paths:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            print(f"skip {p}: {e}", file=sys.stderr)
            continue
        meta, body = parse_frontmatter(text)
        parsed[p] = (meta, body)
        all_bodies[_slug(p)] = body

    low_scorers: list[tuple[str, float]] = []
    changed = 0
    for p, (meta, body) in parsed.items():
        slug = _slug(p)
        score = compute_confidence(
            source_count=_source_count(meta),
            source_qualities=_infer_source_quality(meta, body),
            last_updated=_last_updated(meta, p),
            inbound_links=_count_inbound_links(slug, all_bodies),
        )
        prev = meta.get("confidence")
        try:
            prev_f = float(prev) if prev is not None else None
        except (TypeError, ValueError):
            prev_f = None

        if min_score is not None and score < min_score:
            low_scorers.append((str(p.relative_to(REPO_ROOT)), score))

        if prev_f is not None and abs(prev_f - score) < 0.01:
            continue  # no change

        if verbose or dry_run:
            print(f"{p.relative_to(REPO_ROOT)}: {prev_f!r} → {score}")

        if not dry_run:
            meta["confidence"] = score
            _write_frontmatter(p, meta, body)
        changed += 1

    print(f"\nbackfill{'(dry run)' if dry_run else ''}: {changed}/{len(parsed)} pages updated")
    if low_scorers:
        print(f"\n{len(low_scorers)} pages below {min_score}:")
        for path, s in sorted(low_scorers, key=lambda x: x[1]):
            print(f"  {s:.2f}  {path}")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="llmwiki-confidence-backfill",
        description="Backfill confidence scores into wiki/ frontmatter.",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Report changes without writing")
    ap.add_argument("--only", type=Path, default=None,
                    help="Restrict to a subdirectory (default: wiki/)")
    ap.add_argument("--min-score", type=float, default=None,
                    help="Exit 2 if any page scores below this threshold")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    return backfill(
        dry_run=args.dry_run,
        only=args.only,
        min_score=args.min_score,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
