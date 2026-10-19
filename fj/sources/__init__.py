"""Source adapters: turn a query/feed/playlist into candidate videos."""
from __future__ import annotations

from .base import Candidate, harvest
from . import reddit, archive, playlist, listfile

ADAPTERS = {
    "reddit": reddit,
    "archive": archive,
    "playlist": playlist,
    "list": listfile,
}


def get(name: str):
    mod = ADAPTERS.get(name)
    if mod is None:
        raise KeyError(f"unknown source '{name}' (have: {', '.join(ADAPTERS)})")
    return mod
