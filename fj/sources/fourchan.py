"""4chan adapter — pulls webm/mp4 posts out of live threads.

The JSON API is public and unauthenticated:
  catalogue  https://a.4cdn.org/{board}/catalog.json
  thread     https://a.4cdn.org/{board}/thread/{no}.json
  media      https://i.4cdn.org/{board}/{tim}{ext}

Only the first ~150 posts in a thread carry `tim`/`ext`, and threads expire
within days, so harvest often and let the url_key dedupe.

Usage:
  fj harvest fourchan wsg --limit 40 --threads 6
  fj harvest fourchan wsg --filter "webm dump" --limit 60

Note: /wsg/ is nominally worksafe but reposts from other boards; pick a board
you trust, and remember nothing downstream can judge the content for you.
"""
from __future__ import annotations

import re
import time

from ..util import http_json, now_iso
from .base import Candidate

API = "https://a.4cdn.org"
CDN = "https://i.4cdn.org"
VIDEO_EXT = {".webm", ".mp4", ".m4v"}
POLITE_DELAY = 1.1  # 4chan asks for <= 1 request/second


def _name(filename: str | None, subject: str, board: str, tim) -> str:
    """Filenames are often descriptive; when they're just digits, use the thread."""
    fn = (filename or "").strip()
    if fn and len(fn) > 2 and not re.fullmatch(r"[\d_\-. ]+", fn):
        return fn[:120]
    if subject:
        return f"{subject[:100]} · {str(tim)[-6:]}"
    return f"/{board}/ {tim}"


def _catalog(board: str) -> list[dict]:
    pages = http_json(f"{API}/{board}/catalog.json")
    return [t for page in pages for t in page.get("threads", [])]


def harvest(cfg, board: str = "wsg", filter: str | None = None,
            limit: int = 50, threads: int = 12, min_replies: int = 0,
            **_ignored) -> list[Candidate]:
    board = (board or "wsg").strip().strip("/").split("/")[0]
    if not board:
        raise ValueError("need a board, e.g. wsg")

    cat = _catalog(board)
    cat.sort(key=lambda t: -(t.get("images") or 0))
    if filter:
        needle = filter.lower()
        cat = [t for t in cat
               if needle in (t.get("sub") or "").lower()
               or needle in (t.get("com") or "").lower()]
    if min_replies:
        cat = [t for t in cat if (t.get("replies") or 0) >= min_replies]

    out: list[Candidate] = []
    scanned = 0
    for t in cat:
        if len(out) >= limit or scanned >= threads:
            break
        try:
            d = http_json(f"{API}/{board}/thread/{t['no']}.json")
        except Exception:
            continue
        scanned += 1
        subject = (t.get("sub") or "").replace("<br>", " ").strip()
        for p in d.get("posts", []):
            ext = (p.get("ext") or "").lower()
            if ext not in VIDEO_EXT or not p.get("tim"):
                continue
            if (p.get("fsize") or 0) > 400 * 1024 ** 2:
                continue
            name = _name(p.get("filename"), subject, board, p["tim"])
            out.append({
                "url": f"{CDN}/{board}/{p['tim']}{ext}",
                "source": "fourchan",
                "source_id": f"{board}-{p['tim']}",
                "title": name[:120],
                "duration": None,
                "score": t.get("replies") or 0,
                "tags": board,
                "note": f"/{board}/ thread {t['no']}"
                        + (f" · {subject[:80]}" if subject else "")
                        + f" · {round((p.get('fsize') or 0)/1e6, 1)} MB",
            })
            if len(out) >= limit:
                break
        time.sleep(POLITE_DELAY)
    return out
