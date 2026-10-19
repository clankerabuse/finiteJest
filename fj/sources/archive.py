"""Internet Archive adapter: advancedsearch API -> item detail pages.

Archive items are the best legal source of genuinely strange video: industrial
films, local TV, church tapes, camcorder uploads, software tutorials, etc.

  fj harvest archive --query 'mediatype:movies AND subject:"ephemeral"' --limit 50
  fj harvest archive --collection prelinger --limit 50
"""
from __future__ import annotations

import urllib.parse

from ..util import http_json, looks_like_video_page
from .base import Candidate

SEARCH = "https://archive.org/advancedsearch.php"

SORTS = {
    "downloads": "downloads desc",
    "recent": "addeddate desc",
    "oldest": "addeddate asc",
    "random": "random",
}


def harvest(cfg, query: str | None = None, collection: str | None = None,
            limit: int = 50, sort: str = "downloads", mediatype: str = "movies",
            **_ignored) -> list[Candidate]:
    q = query or f"mediatype:{mediatype}"
    if collection:
        q = f'{q} AND collection:"{collection}"'
    params = [
        ("q", q),
        ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "downloads"),
        ("fl[]", "item_size"), ("fl[]", "date"), ("fl[]", "creator"),
        ("sort[]", SORTS.get(sort, sort)),
        ("rows", str(min(int(limit), 500))),
        ("page", "1"),
        ("output", "json"),
    ]
    url = f"{SEARCH}?{urllib.parse.urlencode(params)}"
    j = http_json(url)
    docs = (j.get("response") or {}).get("docs") or []

    out: list[Candidate] = []
    for d in docs:
        ident = d.get("identifier")
        if not ident:
            continue
        page = f"https://archive.org/details/{ident}"
        if not looks_like_video_page(page):
            continue
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = ", ".join(map(str, creator[:2]))
        out.append({
            "url": page,
            "source": "archive",
            "source_id": ident,
            "title": d.get("title") or ident,
            "uploader": creator,
            "score": d.get("downloads"),
            "published": (d.get("date") or "")[:10] or None,
            "tags": (collection or "archive").lower(),
        })
    return out
