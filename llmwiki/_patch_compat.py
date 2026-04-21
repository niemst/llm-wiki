"""Version-independent compat shim for Bartek's borrowing patches.

Pratiyush/llm-wiki added `llmwiki._frontmatter` in a recent refactor (PR #273).
Engines pinned to older tags don't have it. This module provides the same
`parse_frontmatter(text) -> (meta, body)` API, preferring the upstream
implementation when present and falling back to a stdlib-only parser.

Import as:
    from llmwiki._patch_compat import parse_frontmatter
"""

from __future__ import annotations

import re
from typing import Any

try:
    from llmwiki._frontmatter import parse_frontmatter  # noqa: F401 — re-export
except Exception:  # pragma: no cover — older engine
    _FM_RE = re.compile(r"^---\n?(.*?)\n?---\n?(.*)$", re.DOTALL)
    _KV_RE = re.compile(r"^([a-zA-Z_][\w-]*):\s*(.*)$")

    def _scalar(raw: str) -> Any:
        s = raw.strip()
        if not s:
            return ""
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            if not inner:
                return []
            return [_scalar(x) for x in inner.split(",")]
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        m = _FM_RE.match(text)
        if not m:
            return {}, text
        meta_text, body = m.group(1), m.group(2)
        meta: dict[str, Any] = {}
        for line in meta_text.splitlines():
            mm = _KV_RE.match(line)
            if not mm:
                continue
            meta[mm.group(1)] = _scalar(mm.group(2))
        return meta, body
