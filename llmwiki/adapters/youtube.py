"""YouTube adapter — Patch 4a.

Ingests YouTube videos by fetching their transcripts (via yt-dlp) and
treating each video as one source document. Non-session content, so
`is_ai_session = False` (opt-in only — never pulled by default `sync`).

Config in `examples/sessions_config.json`:

    {
      "adapters": {
        "youtube": {
          "watch_list": "~/brain/_inbox/youtube.txt",
          "out_dir": "raw/sources/youtube"
        }
      }
    }

`watch_list` is a plain-text file with one URL per line (blank lines and
lines starting with `#` are ignored). Each run fetches any URL whose
derived filename doesn't already exist in `out_dir`.

Requires `yt-dlp` on PATH (brew install yt-dlp).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llmwiki import REPO_ROOT
from llmwiki.adapters import register
from llmwiki.adapters.base import BaseAdapter


_YTID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def _video_id(url: str) -> str:
    m = _YTID_RE.search(url)
    return m.group(1) if m else hashlib.sha1(url.encode()).hexdigest()[:11]


@register("youtube")
class YouTubeAdapter(BaseAdapter):
    """YouTube — fetches transcripts for URLs listed in a watch file"""

    is_ai_session = False
    session_store_path = Path("~/brain/_inbox/youtube.txt")

    DEFAULT_OUT = REPO_ROOT / "raw" / "sources" / "youtube"

    @classmethod
    def is_available(cls) -> bool:
        if not shutil.which("yt-dlp"):
            return False
        watch = Path(cls.session_store_path).expanduser()
        return watch.exists()

    def _watch_urls(self) -> list[str]:
        watch = Path(self.config.get("watch_list") or self.session_store_path).expanduser()
        if not watch.exists():
            return []
        urls: list[str] = []
        for line in watch.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                urls.append(s)
        return urls

    def _out_dir(self) -> Path:
        p = Path(self.config.get("out_dir") or self.DEFAULT_OUT)
        if not p.is_absolute():
            p = REPO_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def discover_sessions(self) -> list[Path]:
        """Fetch any new URLs and return the list of markdown source files."""
        out_dir = self._out_dir()
        for url in self._watch_urls():
            vid = _video_id(url)
            md_path = out_dir / f"{vid}.md"
            if md_path.exists():
                continue
            self._fetch(url, vid, md_path)
        return sorted(out_dir.glob("*.md"))

    def _fetch(self, url: str, vid: str, md_path: Path) -> None:
        try:
            info = subprocess.run(
                ["yt-dlp", "-j", "--skip-download", url],
                capture_output=True, text=True, timeout=60, check=True,
            )
            meta = json.loads(info.stdout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return

        title = meta.get("title") or vid
        channel = meta.get("channel") or meta.get("uploader") or "unknown"
        upload_date = meta.get("upload_date") or ""  # YYYYMMDD
        date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if len(upload_date) == 8 else ""

        # Try to grab subtitles (auto or manual) as VTT.
        tmp = md_path.parent / f".{vid}"
        tmp.mkdir(exist_ok=True)
        transcript = ""
        try:
            subprocess.run(
                ["yt-dlp", "--skip-download",
                 "--write-auto-sub", "--write-sub",
                 "--sub-lang", "en.*", "--sub-format", "vtt",
                 "-o", str(tmp / "%(id)s.%(ext)s"),
                 url],
                capture_output=True, timeout=120, check=False,
            )
            for vtt in tmp.glob(f"{vid}*.vtt"):
                transcript = _vtt_to_text(vtt.read_text(encoding="utf-8"))
                break
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        front = [
            "---",
            f'title: "{title.replace(chr(34), chr(39))}"',
            "type: source",
            "source_kind: youtube",
            f"url: {url}",
            f"channel: {channel}",
            f"date: {date or datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "---",
            "",
            f"# {title}",
            "",
            f"**Channel:** {channel}  ",
            f"**URL:** {url}  ",
            "",
            "## Transcript",
            "",
            transcript or "_(no transcript available)_",
        ]
        md_path.write_text("\n".join(front), encoding="utf-8")

    def derive_project_slug(self, path: Path) -> str:
        return "youtube"


def _vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    prev = ""
    for line in vtt.splitlines():
        s = line.strip()
        if not s or s.startswith(("WEBVTT", "NOTE")) or "-->" in s or s.isdigit():
            continue
        if s == prev:
            continue
        lines.append(s)
        prev = s
    return " ".join(lines)
