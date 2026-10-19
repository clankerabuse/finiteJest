"""Generic "anything yt-dlp understands" adapter.

Point it at a channel, playlist, user, search URL or archive.org collection and
it enumerates the entries flat (fast, no per-video extraction):

  fj harvest playlist "https://www.youtube.com/@SomeWeirdChannel/videos" --limit 60
  fj harvest playlist "ytsearch40:unsettling public access tv" --limit 40
  fj harvest playlist "https://archive.org/details/prelinger" --limit 50
"""
from __future__ import annotations

from .. import ytdlp
from .base import Candidate


def harvest(cfg, url: str, limit: int = 50, tag: str | None = None,
            **_ignored) -> list[Candidate]:
    if not url:
        raise ValueError("need a playlist/channel/search URL")
    info = ytdlp.probe(url, flat=True, cookies=cfg["video"].get("cookies_from_browser", ""),
                       playlist_end=int(limit))
    entries = ytdlp.flat_entries(info)[:int(limit)]
    out: list[Candidate] = []
    origin = url.split("//")[-1].split("/")[0].replace("www.", "")
    for e in entries:
        out.append({
            "url": e["url"],
            "source": "playlist",
            "source_id": e.get("source_id"),
            "title": e.get("title"),
            "uploader": e.get("uploader"),
            "duration": e.get("duration"),
            "score": e.get("score"),
            "tags": (tag or origin).lower(),
            "note": f"via {url[:120]}",
        })
    return out
