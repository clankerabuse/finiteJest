"""Adapter contract.

An adapter returns a list of Candidate dicts. It must never download media and
should be cheap to re-run: the DB dedupes by url_key.
"""
from __future__ import annotations

import typing as t


class Candidate(t.TypedDict, total=False):
    url: str
    source: str
    source_id: str | None
    title: str | None
    uploader: str | None
    duration: float | None
    score: int | None
    tags: str
    note: str | None


def harvest(cfg: dict, **kwargs) -> list[Candidate]:
    raise NotImplementedError
