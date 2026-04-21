"""Generic article adapter — Patch 4c.

Ingests any URL listed in a watch file by fetching rendered markdown
through the `r.jina.ai` reader-proxy (or a user-configured alternative).
Good fallback for blogs, docs, and any web page that doesn't have a
specialized adapter.

Config:

    {
      "adapters": {
        "article": {
          "watch_list": "~/brain/_inbox/articles.txt",
          "out_dir": "raw/sources/articles",
          "mirror": "https://r.jina.ai/"
        }
      }
    }
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from llmwiki import REPO_ROOT
from llmwiki.adapters import register
from llmwiki.adapters.base import BaseAdapter


@register("article")
class ArticleAdapter(BaseAdapter):
    """Article — fetches arbitrary URLs via reader-proxy into source markdown"""

    is_ai_session = False
    session_store_path = Path("~/brain/_inbox/articles.txt")

    DEFAULT_OUT = REPO_ROOT / "raw" / "sources" / "articles"
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
            md = out / f"{_slug(url)}.md"
            if md.exists():
                continue
            self._fetch(url, md)
        return sorted(out.glob("*.md"))

    def _fetch(self, url: str, md: Path) -> None:
        try:
            res = subprocess.run(
                ["curl", "-sSL", "--max-time", "60", self._mirror() + url],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            return
        body = res.stdout.strip()
        if not body:
            return

        host = urlparse(url).netloc or "unknown"
        # Reader proxy tends to start with a title line; keep as-is.
        first_line = body.splitlines()[0] if body.splitlines() else url
        title = re.sub(r"^Title:\s*", "", first_line).strip()[:120] or host

        front = [
            "---",
            f'title: "{title.replace(chr(34), chr(39))}"',
            "type: source",
            "source_kind: article",
            f"url: {url}",
            f"domain: {host}",
            f"date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "---",
            "",
            body,
        ]
        md.write_text("\n".join(front), encoding="utf-8")

    def derive_project_slug(self, path: Path) -> str:
        return "articles"


def _slug(url: str) -> str:
    host = urlparse(url).netloc.replace(".", "-")
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{host}-{h}" if host else h
