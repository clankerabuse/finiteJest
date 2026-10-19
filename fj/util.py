"""Small helpers: sizes, URLs, hashing, HTTP."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- sizes
_UNITS = ["B", "KiB", "MiB", "GiB", "TiB"]


def human_size(n) -> str:
    if n is None:
        return "?"
    f = float(n)
    i = 0
    while f >= 1024 and i < len(_UNITS) - 1:
        f /= 1024
        i += 1
    return f"{f:.0f} {_UNITS[i]}" if i == 0 else f"{f:.1f} {_UNITS[i]}"


_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([kmgt]?)(i?b)?\s*$", re.I)


def parse_size(s) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    m = _SIZE_RE.match(str(s))
    if not m:
        raise ValueError(f"bad size: {s!r}")
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
    return int(float(m.group(1)) * mult[m.group(2).lower()])


def gib(n: float) -> int:
    return int(n * 1024 ** 3)


# ---------------------------------------------------------------- urls
_TRACKING = ("utm_", "fbclid", "gclid", "igshid", "mc_cid", "mc_eid")


def canonical_key(url: str) -> str:
    """Stable identity for a video, so the same video from two sources dedupes."""
    url = (url or "").strip()
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    q = urllib.parse.parse_qs(u.query)

    if host in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        if u.path == "/watch" and q.get("v"):
            return "youtube:" + q["v"][0]
        m = re.match(r"^/(?:shorts|embed|live|v)/([\w-]{6,})", u.path)
        if m:
            return "youtube:" + m.group(1)
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
        if vid:
            return "youtube:" + vid
    if host == "archive.org":
        m = re.match(r"^/details/([^/]+)", u.path)
        if m:
            return "archive:" + m.group(1)
    if host.endswith("v.redd.it"):
        return "reddit-video:" + u.path.strip("/")
    if host in ("vimeo.com",) and re.match(r"^/\d+", u.path):
        return "vimeo:" + u.path.strip("/").split("/")[0]

    keep = {k: v for k, v in q.items()
            if not k.lower().startswith(_TRACKING) and k.lower() != "ref"}
    path = u.path.rstrip("/") or "/"
    tail = "?" + urllib.parse.urlencode(sorted(keep.items()), doseq=True) if keep else ""
    return f"{host}{path}{tail}"


def looks_like_video_page(url: str) -> bool:
    """Cheap filter for links found in feeds/forums."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").lower()
    bad = ("i.redd.it", "imgur.com", "i.imgur", "twitter.com", "x.com",
           "instagram.com", "facebook.com", "tiktok.com")
    if any(host == b or host.endswith("." + b) for b in bad):
        return False
    return True


# ---------------------------------------------------------------- http
def http_json(url: str, headers: dict | None = None, retries: int = 3,
              timeout: int = 30, data: bytes | None = None):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=hdrs, data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            last = f"HTTP {e.code} {e.reason} for {url} :: {body[:120]}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last) from None
        except Exception as e:  # network hiccup
            last = f"{type(e).__name__}: {e} for {url}"
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last or f"request failed: {url}")


def head_filesize(url: str, timeout: int = 20) -> int | None:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except Exception:
        return None


# ---------------------------------------------------------------- files
def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def which(*names) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def ffprobe(path: str) -> dict:
    """Return {duration, width, height} best-effort; {} if ffprobe is absent."""
    exe = which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=120).stdout
        d = json.loads(out or "{}")
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        dur = d.get("format", {}).get("duration") or v.get("duration")
        return {"duration": float(dur) if dur else None,
                "width": v.get("width"), "height": v.get("height")}
    except Exception:
        return {}


def fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def slug(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip()).strip("-")
    return (s or "untitled")[:maxlen].lower()


def ensure_dir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p
