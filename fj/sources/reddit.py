"""Reddit adapter.

Reddit blocks unauthenticated JSON from most datacenter/residential IPs now, so
the supported path is a free "script" app:

  https://www.reddit.com/prefs/apps -> create app -> type: script
  put the id/secret in config.toml under [reddit] (redirect uri: http://localhost:8080)

We use the client_credentials grant, which reads public listings without a user
login. Without credentials we still try the public .json endpoints and fail
loudly with the setup hint.
"""
from __future__ import annotations

import base64
import urllib.parse

from ..util import canonical_key, http_json, looks_like_video_page, UA
from .base import Candidate

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH = "https://oauth.reddit.com"
PUBLIC = "https://www.reddit.com"


def _token(cfg) -> str | None:
    cid = cfg["reddit"].get("client_id") or ""
    sec = cfg["reddit"].get("client_secret") or ""
    if not (cid and sec):
        return None
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    j = http_json(TOKEN_URL, headers={
        "Authorization": f"Basic {auth}",
        "User-Agent": cfg["reddit"]["user_agent"],
        "Content-Type": "application/x-www-form-urlencoded",
    }, data=data)
    tok = j.get("access_token")
    if not tok:
        raise RuntimeError(f"reddit auth failed: {j}")
    return tok


def _listing(cfg, sub: str, sort: str, timeframe: str, limit: int) -> list[dict]:
    """Page through a listing until `limit` posts or the cursor runs out."""
    tok = _token(cfg)
    headers = {"User-Agent": cfg["reddit"]["user_agent"]}
    if tok:
        headers["Authorization"] = f"bearer {tok}"
        base = f"{OAUTH}/r/{sub}/{sort}"
    else:
        base = f"{PUBLIC}/r/{sub}/{sort}.json"

    out: list[dict] = []
    after = None
    while len(out) < limit:
        page = min(100, limit - len(out))
        q = {"limit": str(page), "raw_json": "1"}
        if sort in ("top", "controversial"):
            q["t"] = timeframe
        if after:
            q["after"] = after
        j = http_json(f"{base}?{urllib.parse.urlencode(q)}", headers=headers)
        children = (j.get("data") or {}).get("children") or []
        if not children:
            break
        out += [c.get("data", {}) for c in children]
        after = (j.get("data") or {}).get("after")
        if not after:
            break
    return out[:limit]


def harvest(cfg, sub: str, sort: str = "top", timeframe: str = "all",
            limit: int = 100, min_score: int | None = None,
            **_ignored) -> list[Candidate]:
    if not sub:
        raise ValueError("need a subreddit")
    sub = sub.removeprefix("r/").strip("/")
    min_score = cfg["reddit"]["min_score"] if min_score is None else min_score
    try:
        posts = _listing(cfg, sub, sort, timeframe, limit)
    except RuntimeError as e:
        if "403" in str(e) or "401" in str(e):
            raise RuntimeError(
                "reddit refused anonymous access. Create a free script app at "
                "https://www.reddit.com/prefs/apps and put client_id/client_secret "
                "in config.toml [reddit] (or export FJ_REDDIT_ID/FJ_REDDIT_SECRET)."
            ) from None
        raise

    out: list[Candidate] = []
    for p in posts:
        if p.get("stickied") or p.get("over_18") is None and False:
            continue
        if (p.get("score") or 0) < min_score:
            continue
        if p.get("is_self") and not p.get("url_overridden_by_dest"):
            continue
        url = p.get("url_overridden_by_dest") or p.get("url") or ""
        if p.get("is_video") and "v.redd.it" in url:
            url = "https://www.reddit.com" + (p.get("permalink") or "")
        if not looks_like_video_page(url):
            continue
        out.append({
            "url": url,
            "source": "reddit",
            "source_id": p.get("id"),
            "title": p.get("title"),
            "score": p.get("score"),
            "tags": sub.lower(),
            "note": f"r/{sub} · {p.get('num_comments', 0)} comments",
        })
    return out


def harvest_many(cfg, subs: list[str], **kw) -> list[Candidate]:
    out: list[Candidate] = []
    for s in subs:
        try:
            out += harvest(cfg, s, **kw)
        except Exception as e:
            print(f"  ! r/{s}: {e}")
    return out
