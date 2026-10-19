"""Plain text list adapter: one URL per line, '#' comments ignored."""
from __future__ import annotations

import os

from ..util import looks_like_video_page
from .base import Candidate


def harvest(cfg, path: str, tag: str | None = None, **_ignored) -> list[Candidate]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    out: list[Candidate] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            if not looks_like_video_page(url):
                continue
            out.append({"url": url, "source": "list", "title": None,
                        "tags": (tag or os.path.basename(path)).lower()})
    return out
