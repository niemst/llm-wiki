"""X (Twitter) adapter — Patch 4b.

Ingests X/Twitter threads listed in a watch file. Uses the public
read-only endpoint via `curl` + a JSON parser — no API key required
for public posts.

Config:

    {
      "adapters": {
        "x": {
          "watch_list": "~/brain/_inbox/x.txt",
          "out_dir": "raw/sources/x",
          "mirror": "https://r.jina.ai/"  // reader-proxy; swap if needed
        }
      }
    }

Each line in the watch file is an X status URL (or a nitter/jina.ai
equivalent). The adapter fetches rendered text via the configured
mirror and writes one markdown source per URL.

Note: X aggressively blocks scraping. The reader-proxy pattern
(`https://r.jina.ai/<full-url>`) returns plain markdown and is more
reliable than direct HTML scraping. If you have a different mirror,
override via config.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from llmwiki import REPO_ROOT
from llmwiki.adapters import register
from llmwiki.adapters.base import BaseAdapter


_STATUS_RE = re.compile(r"(?:twitter|x)\.com/([^/]+)/status/(\d+)")


@register("x")
class XAdapter(BaseAdapter):
    """X / Twitter — fetches threads listed in a watch file"""

    is_ai_session = False
    session_store_path = Path("~/brain/_inbox/x.txt")

    DEFAULT_OUT = REPO_ROOT / "raw" / "sources" / "x"
    DEFAULT_MIRROR = "https://r.jina.ai/"

    @classmethod
    def is_available(cls) -> bool:
        if not shutil.which("curl"):
            return False
        return Path(cls.session_store_path).expanduser().exists()

    def _urls(self) -> list[str]:
        watch = Path(self.config.get("watch_list") or self.session_store_path).expanduser()
        if not watch.exists():
            return []
        return [l.strip() for l in watch.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]

    def _out(self) -> Path:
        p = Path(self.config.get("out_dir") or self.DEFAULT_OUT)
        if not p.is_absolute():
            p = REPO_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _mirror(self) -> str:
        return self.config.get("mirror", self.DEFAULT_MIRROR).rstrip("/") + "/"

    def discover_sessions(self) -> list[Path]:
        out = self._out()
        for url in self._urls():
            slug = _slug(url)
            md = out / f"{slug}.md"
            if md.exists():
                continue
            self._fetch(url, md)
        return sorted(out.glob("*.md"))

    def _fetch(self, url: str, md: Path) -> None:
        proxied = self._mirror() + url
        try:
            res = subprocess.run(
                ["curl", "-sSL", "--max-time", "45", proxied],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            return
        body = res.stdout.strip()
        if not body:
            return

        m = _STATUS_RE.search(url)
        user = m.group(1) if m else "unknown"
        status = m.group(2) if m else ""

        front = [
            "---",
            f'title: "X thread by @{user} ({status})"',
            "type: source",
            "source_kind: x",
            f"url: {url}",
            f"author: {user}",
            f"date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "---",
            "",
            f"# X thread by @{user}",
            "",
            f"**URL:** {url}",
            "",
            body,
        ]
        md.write_text("\n".join(front), encoding="utf-8")

    def derive_project_slug(self, path: Path) -> str:
        return "x"


def _slug(url: str) -> str:
    m = _STATUS_RE.search(url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return hashlib.sha1(url.encode()).hexdigest()[:12]
