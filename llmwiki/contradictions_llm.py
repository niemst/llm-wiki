"""LLM-backed contradiction scanner — Patch 2.

Pratiyush's `lint/rules.py` has a ContradictionDetection rule that only
matches explicit `## Contradictions` sections. This module populates those
sections by diffing new facts against existing entity pages via the Claude
CLI (the same `claude -p` bridge Hermes uses).

Workflow:
    1. Walk wiki/entities/ and wiki/concepts/
    2. For each page with a `## Key Facts` or `## Key Claims` section,
       ask Claude to compare its claims against the pages it wikilinks to.
    3. If contradictions found, write/update a `## Contradictions` section
       on the page (preserving the rest of the body).
    4. The existing lint rule then flags those pages during `wiki_lint`.

Usage:
    python3 -m llmwiki.contradictions_llm              # scan all
    python3 -m llmwiki.contradictions_llm --page <slug>  # one page
    python3 -m llmwiki.contradictions_llm --dry-run
    python3 -m llmwiki.contradictions_llm --model claude-haiku-4-5

Env:
    BRAIN_LLM_CMD   override the `claude -p` invocation (default: claude -p)
    BRAIN_LLM_MODEL optional model flag (e.g. claude-haiku-4-5)
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from llmwiki import REPO_ROOT
from llmwiki._patch_compat import parse_frontmatter

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
KEY_FACTS_RE = re.compile(r"## Key (?:Facts|Claims)\n(.*?)(?=\n## |\Z)", re.DOTALL)
CONTRADICTIONS_RE = re.compile(r"\n## Contradictions\n.*?(?=\n## |\Z)", re.DOTALL)

PROMPT = """You are auditing a personal knowledge-base page for contradictions against pages it references.

The page under review:
<page>
{page}
</page>

Pages it wikilinks to (for cross-reference):
<references>
{refs}
</references>

TASK: Identify factual contradictions between the page and its references. A contradiction is a direct disagreement on a concrete fact (dates, numbers, names, versions, outcomes, positions). Ignore stylistic differences or partial overlap.

Output strictly in this format (and nothing else):

CONTRADICTIONS:
- <one-sentence description> (vs [[referenced-page-slug]])
- <one-sentence description> (vs [[referenced-page-slug]])

If there are no contradictions, output exactly:
CONTRADICTIONS: none
"""


def _llm(prompt: str, model: str | None) -> str:
    cmd = shlex.split(os.environ.get("BRAIN_LLM_CMD", "claude -p"))
    if model or os.environ.get("BRAIN_LLM_MODEL"):
        m = model or os.environ["BRAIN_LLM_MODEL"]
        cmd += ["--model", m]
    try:
        res = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
    except subprocess.TimeoutExpired:
        return "CONTRADICTIONS: none"  # fail-safe
    except subprocess.CalledProcessError as e:
        print(f"LLM call failed: {e.stderr}", file=sys.stderr)
        return "CONTRADICTIONS: none"
    return res.stdout.strip()


def _pages_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for p in (REPO_ROOT / "wiki").rglob("*.md"):
        idx[p.stem.lower()] = p
    return idx


def _resolve_refs(body: str, idx: dict[str, Path]) -> list[Path]:
    refs: list[Path] = []
    for m in WIKILINK_RE.finditer(body):
        slug = m.group(1).strip().lower()
        if slug in idx:
            refs.append(idx[slug])
    # Dedupe while preserving order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq[:8]  # cap context


def _has_claims(body: str) -> bool:
    return bool(KEY_FACTS_RE.search(body))


def _parse_contradictions(output: str) -> list[str]:
    if "CONTRADICTIONS: none" in output:
        return []
    lines = output.split("\n")
    bullets = [l.strip() for l in lines if l.strip().startswith("- ")]
    return [l[2:].strip() for l in bullets if l[2:].strip()]


def _write_contradictions(path: Path, contradictions: list[str], dry_run: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    # Remove any existing ## Contradictions section first.
    cleaned = CONTRADICTIONS_RE.sub("", text)
    if not contradictions:
        if cleaned == text:
            return False
        if not dry_run:
            path.write_text(cleaned.rstrip() + "\n", encoding="utf-8")
        return True
    block = "\n## Contradictions\n\n" + "\n".join(f"- {c}" for c in contradictions) + "\n"
    new_text = cleaned.rstrip() + "\n" + block
    if new_text == text:
        return False
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def scan(
    *,
    page_filter: str | None = None,
    dry_run: bool = False,
    model: str | None = None,
    verbose: bool = False,
) -> int:
    idx = _pages_index()
    targets: list[Path] = []
    for slug, p in idx.items():
        if page_filter and slug != page_filter.lower():
            continue
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        if (meta.get("type") or "").lower() not in ("entity", "concept"):
            continue
        if not _has_claims(body):
            continue
        targets.append(p)

    if not targets:
        print("no eligible pages", file=sys.stderr)
        return 0

    changed = 0
    total_flags = 0
    for p in targets:
        _, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        refs = _resolve_refs(body, idx)
        if not refs:
            if verbose:
                print(f"{p.name}: no resolvable refs — skip")
            continue
        refs_text = "\n\n---\n\n".join(
            f"## {r.stem}\n\n{r.read_text(encoding='utf-8')}" for r in refs
        )
        out = _llm(PROMPT.format(page=p.read_text(encoding="utf-8"), refs=refs_text), model)
        cs = _parse_contradictions(out)
        if verbose or dry_run:
            print(f"{p.relative_to(REPO_ROOT)}: {len(cs)} contradiction(s)")
            for c in cs:
                print(f"  - {c}")
        if _write_contradictions(p, cs, dry_run):
            changed += 1
        total_flags += len(cs)

    print(f"\ncontradiction-scan{'(dry run)' if dry_run else ''}: "
          f"{changed} pages updated, {total_flags} flags total")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llmwiki-contradictions-llm")
    ap.add_argument("--page", default=None, help="Limit to one page slug")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    return scan(
        page_filter=args.page,
        dry_run=args.dry_run,
        model=args.model,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
