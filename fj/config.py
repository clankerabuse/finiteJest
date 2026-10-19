"""Config loading: config.toml merged over sane defaults."""
from __future__ import annotations

import os
import tomllib
from copy import deepcopy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "general": {
        "budget_gib": 15.0,      # hard ceiling for downloaded media
        "target_count": 100,     # stop after this many videos
        "max_attempts": 3,
        "min_duration_s": 3,
        "max_duration_s": 3600,
        "concurrency": 1,        # downloads are sequential on purpose (be polite)
    },
    "video": {
        "format": ("bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]"
                   "/bv*[height<=1080]+ba/b[height<=1080]/b"),
        "merge": "mp4",
        "max_height": 1080,
        "max_file_mib": 800,
        "thumbs": False,
        "cookies_from_browser": "",   # e.g. "firefox" when a site demands login
    },
    "reddit": {
        "client_id": "",
        "client_secret": "",
        "user_agent": "linux:finitejest:0.1 (video archiving)",
        "min_score": 25,
        "subreddits": [],             # default harvest list
    },
    "paths": {
        "videos": "data/videos",
        "meta": "data/meta",
        "exports": "collection",
        "db": "data/finitejest.db",
    },
}


def _merge(base: dict, override: dict) -> dict:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path: str | None = None) -> dict:
    cfg = deepcopy(DEFAULTS)
    path = path or os.path.join(ROOT, "config.toml")
    if os.path.exists(path):
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    # secrets may live in the environment instead of the file
    if os.environ.get("FJ_REDDIT_ID"):
        cfg["reddit"]["client_id"] = os.environ["FJ_REDDIT_ID"]
    if os.environ.get("FJ_REDDIT_SECRET"):
        cfg["reddit"]["client_secret"] = os.environ["FJ_REDDIT_SECRET"]
    return cfg


def path(cfg: dict, key: str) -> str:
    p = cfg["paths"][key]
    return p if os.path.isabs(p) else os.path.join(ROOT, p)
