"""Generic booru adapter (gelbooru v0.2 / danbooru / e621 flavours).

Covers the soyjak end of the boorus as well as classic ones: a post is just a
file_url plus tags, and video posts are webm/mp4.

  fj harvest booru https://e621.net --tags "webm" --limit 40
  fj harvest booru https://xbooru.com --tags "webm" --limit 40
  fj harvest booru https://soybooru.com --tags "animated"   # needs a CF cookie

Cloudflare-walled boorus (soybooru, danbooru, rule34) answer 403 to anything
that isn't a browser. If you have a session, paste the raw Cookie header into
config.toml [booru] cookie and the adapter sends it; otherwise the site is
simply unreachable from a script and should be skipped.
"""
from __future__ import annotations

import re
import urllib.parse

from ..util import http_json
from .base import Candidate

VIDEO_EXT = {"webm", "mp4", "m4v", "mov"}


def _ext(url: str, hint: str | None = None) -> str:
    if hint:
        return hint.lower().lstrip(".")
    path = urllib.parse.urlsplit(url or "").path
    return path.rsplit(".", 1)[-1].lower() if "." in path else ""


def _headers(cfg) -> dict:
    h = {"Accept": "application/json"}
    cookie = (cfg.get("booru", {}) or {}).get("cookie") or ""
    if cookie:
        h["Cookie"] = cookie
    return h


def _title(host: str, pid, tags: str) -> str:
    """Booru posts have no title; tags are the metadata, so keep the label neutral."""
    return f"{host} #{pid}"


def _tag_str(tags) -> str:
    """Normalise: gelbooru returns space-separated tags, danbooru/e621 commas."""
    if isinstance(tags, list):
        flat = [str(t) for t in tags]
    else:
        flat = re.split(r"[,\s]+", str(tags or ""))
    return ", ".join(t for t in (x.strip() for x in flat) if t)[:160]


def _from_danbooru(posts: list, site: str, limit: int) -> list[Candidate]:
    out = []
    for p in posts[:limit]:
        url = p.get("file_url") or p.get("large_file_url") or ""
        if _ext(url, p.get("file_ext")) not in VIDEO_EXT:
            continue
        tags = _tag_str(p.get("tag_string") or p.get("tag_string_general"))
        out.append({
            "url": url,
            "source": "booru",
            "source_id": f"{urllib.parse.urlsplit(site).hostname}-{p.get('id')}",
            "title": _title(urllib.parse.urlsplit(site).hostname or "booru",
                                  p.get("id"), p.get("tag_string_character") or tags),
            "score": p.get("score") or p.get("fav_count"),
            "tags": tags,
            "note": site,
        })
    return out


def _from_e621(posts: list, site: str, limit: int) -> list[Candidate]:
    out = []
    for p in posts[:limit]:
        f = p.get("file") or {}
        url = f.get("url") or ""
        if _ext(url, f.get("ext")) not in VIDEO_EXT:
            continue
        tags = _tag_str((p.get("tags") or {}).get("general"))
        out.append({
            "url": url,
            "source": "booru",
            "source_id": f"{urllib.parse.urlsplit(site).hostname}-{p.get('id')}",
            "title": _title(urllib.parse.urlsplit(site).hostname or "booru",
                                  p.get("id"), tags),
            "score": (p.get("score") or {}).get("total"),
            "tags": tags,
            "note": f"{site} · {f.get('width')}x{f.get('height')}",
        })
    return out


def _from_gelbooru(posts: list, site: str, limit: int) -> list[Candidate]:
    out = []
    for p in posts[:limit]:
        url = p.get("file_url") or ""
        if _ext(url) not in VIDEO_EXT:
            continue
        tags = _tag_str(p.get("tags"))
        out.append({
            "url": url,
            "source": "booru",
            "source_id": f"{urllib.parse.urlsplit(site).hostname}-{p.get('id')}",
            "title": _title(urllib.parse.urlsplit(site).hostname or "booru",
                                  p.get("id"), tags),
            "score": p.get("score"),
            "tags": tags,
            "note": site,
        })
    return out


def _try(url: str, cfg, params: dict):
    q = urllib.parse.urlencode(params)
    sep = "&" if "?" in url else "?"
    return http_json(f"{url}{sep}{q}", headers=_headers(cfg), retries=1)


def _scan(fetch, want: int, start: int = 0, max_pages: int = 3) -> list[Candidate]:
    """Booru searches return mostly images; walk a few pages before giving up."""
    out: list[Candidate] = []
    page = start
    for _ in range(max_pages):
        batch, raw = fetch(page)
        out += batch
        if raw == 0:
            break
        page += 1
        if len(out) >= want:
            break
    return out[:want]


def harvest(cfg, site: str, tags: str = "", limit: int = 50, page: int = 0,
            engine: str = "auto", **_ignored) -> list[Candidate]:
    if not site:
        raise ValueError("need a booru base URL, e.g. https://e621.net")
    site = site.rstrip("/")
    host = (urllib.parse.urlsplit(site).hostname or "").lower()
    want = min(int(limit), 100)
    per_page = min(100, max(40, want * 4))
    errors = []

    def e621(p):
        j = _try(f"{site}/posts.json",
                 cfg, {"tags": tags, "limit": min(320, per_page), "page": p + 1})
        posts = (j or {}).get("posts") or []
        return _from_e621(posts, site, want), len(posts)

    def danbooru(p):
        j = _try(f"{site}/posts.json",
                 cfg, {"tags": tags, "limit": per_page, "page": p + 1})
        posts = j or []
        return _from_danbooru(posts, site, want), len(posts)

    def gelbooru(p):
        j = _try(f"{site}/index.php", cfg, {
            "page": "dapi", "s": "post", "q": "index", "json": "1",
            "limit": per_page, "pid": p, "tags": tags})
        posts = j.get("post", []) if isinstance(j, dict) else j
        if isinstance(posts, dict):      # single result comes back as an object
            posts = [posts]
        posts = posts or []
        return _from_gelbooru(posts, site, want), len(posts)

    order = {
        "e621": [e621],
        "danbooru": [danbooru],
        "gelbooru": [gelbooru],
        "auto": ([e621] if ("e621" in host or "e926" in host)
                 else [danbooru, gelbooru, e621]),
    }.get(engine.lower())
    if order is None:
        raise ValueError(f"unknown engine '{engine}' (auto|gelbooru|danbooru|e621)")

    for fn in order:
        try:
            rows = _scan(fn, want, start=page)
            if rows:
                return rows
            errors.append(f"{fn.__name__}: no video posts")
        except Exception as e:  # try the next flavour
            errors.append(f"{fn.__name__}: {str(e)[:120]}")

    raise RuntimeError(
        f"no booru API answered for {site} — " + " | ".join(errors)
        + ". If the site is behind Cloudflare, set [booru] cookie in config.toml.")
