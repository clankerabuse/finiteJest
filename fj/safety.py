"""Content screening: what the pipeline will and won't let into the collection.

Two layers, both cheap enough to run on every download:

1. text — block/flag terms matched against title, tags and notes *before*
   anything is fetched (a 4chan filename or booru tag is the earliest signal
   you get), and again after probing;
2. bytes — container sniffing, embedded-payload detection, and an ffprobe
   structural check on the finished file before it is allowed into `done`.

Anything that fails is moved to `data/quarantine/` and kept out of the catalog
until a human runs `fj approve`. Anything that merely raises a flag goes there
too — the pipeline's default is "do not silently keep it".

What this cannot do: tell you what is *in* the picture. Detecting illegal
imagery needs hash-matching against NCMEC/PhotoDNA-style databases, which is an
organisational partnership, not a library. Source hygiene (which boards and
tags you permit) and the quarantine review are the real controls; this module
is the mechanical part.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from . import config
from .util import which

# ---------------------------------------------------------------- containers
CONTAINERS = {
    "mp4": {"mp4", "m4v", "mov"},
    "matroska": {"webm", "mkv"},
    "avi": {"avi"},
    "mpeg": {"mpg", "mpeg", "m2v"},
    "flv": {"flv"},
    "ogg": {"ogv", "ogg"},
}

NOT_VIDEO = {"zip", "elf", "pe", "macho", "pdf", "html", "gif", "png", "jpg", "rar", "7z"}

VIDEO_CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video",
                "theora", "prores", "dnxhd", "vc1", "mjpeg"}

# payload signatures that should never appear inside a video container
SUSPICIOUS = [
    (b"PK\x03\x04", "zip archive"),
    (b"\x7fELF", "ELF executable"),
    (b"MZ\x90\x00", "PE executable"),
    (b"<?php", "php code"),
    (b"<script", "html/script"),
    (b"<!DOCTYPE", "html document"),
    (b"%PDF-", "pdf"),
]
SCAN_CHUNK = 4 * 1024 * 1024
HEAD = 64


def sniff(head: bytes) -> str | None:
    if len(head) < 12:
        return None
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return "mpeg"
    if head[:3] == b"FLV":
        return "flv"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:4] == b"\x7fELF":
        return "elf"
    if head[:4] == b"MZ\x90\x00":
        return "pe"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return "macho"
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:5].lower().startswith((b"<!doc", b"<html")):
        return "html"
    if head[:3] == b"GIF":
        return "gif"
    return None


# ---------------------------------------------------------------- terms
def _policy_file(cfg, key: str, default: str) -> str:
    p = (cfg.get("safety", {}) or {}).get(key) or default
    return p if os.path.isabs(p) else os.path.join(config.ROOT, p)


def load_terms(cfg) -> tuple[list[str], list[str]]:
    """Returns (hard_block_terms, review_flag_terms)."""
    out = []
    for key, default in (("block_terms", "policy/block_terms.txt"),
                         ("flag_terms", "policy/flag_terms.txt")):
        terms = []
        path = _policy_file(cfg, key, default)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        terms.append(line.lower())
        out.append(terms)
    return out[0], out[1]


def _hits(text: str, terms: list[str]) -> list[str]:
    if not text or not terms:
        return []
    flat = re.sub(r"[_\-]+", " ", text.lower())
    found = []
    for t in terms:
        pat = r"(?<![a-z0-9])" + r"\s+".join(map(re.escape, t.split())) + r"(?![a-z0-9])"
        if re.search(pat, flat):
            found.append(t)
    return found


def screen_text(cfg, *texts) -> tuple[str, list[str]]:
    """ok | blocked (hard term) | review (soft term)."""
    block, flag = load_terms(cfg)
    blob = " ".join(str(t) for t in texts if t)
    hard = _hits(blob, block)
    if hard:
        return "blocked", hard
    soft = _hits(blob, flag)
    if soft:
        return "review", soft
    return "ok", []


# ---------------------------------------------------------------- bytes
@dataclass
class ScanResult:
    verdict: str = "ok"                       # ok | flag | fail
    findings: list[str] = field(default_factory=list)
    sha256: str | None = None
    size: int = 0
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    container: str | None = None

    def add(self, level: str, msg: str) -> None:
        self.findings.append(msg)
        if level == "fail":
            self.verdict = "fail"
        elif level == "flag" and self.verdict == "ok":
            self.verdict = "flag"

    @property
    def reason(self) -> str:
        return "; ".join(self.findings) or "clean"


def _digest_and_scan(path: str) -> tuple[str, set[str]]:
    """One pass: sha256 + embedded-payload signatures (chunk-boundary safe)."""
    h = hashlib.sha256()
    found: set[str] = set()
    prev = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(SCAN_CHUNK)
            if not chunk:
                break
            h.update(chunk)
            buf = prev + chunk
            for sig, label in SUSPICIOUS:
                if sig in buf:
                    found.add(label)
            prev = buf[-16:]
    return h.hexdigest(), found


def _probe(path: str) -> dict:
    exe = which("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=180).stdout
        return json.loads(out or "{}")
    except Exception:
        return {}


def scan(path: str, cfg) -> ScanResult:
    res = ScanResult(size=os.path.getsize(path))
    if res.size == 0:
        res.add("fail", "empty file")
        return res

    with open(path, "rb") as f:
        head = f.read(HEAD)
    container = sniff(head)
    res.container = container
    ext = os.path.splitext(path)[1].lstrip(".").lower()

    if container is None:
        res.add("fail", "unrecognised container (not a video)")
    elif container in NOT_VIDEO:
        res.add("fail", f"not a video container ({container})")
    elif ext and ext not in CONTAINERS.get(container, set()):
        res.add("flag", f"extension .{ext} disagrees with {container}")

    res.sha256, payloads = _digest_and_scan(path)
    for label in sorted(payloads):
        res.add("fail", f"embedded {label} payload")

    info = _probe(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    fmt = info.get("format") or {}

    if not info:
        res.add("flag", "ffprobe could not read it")
    elif not video:
        res.add("fail", "no video stream (audio only?)")
    else:
        res.codec = video.get("codec_name")
        res.width, res.height = video.get("width"), video.get("height")
        if res.codec and res.codec not in VIDEO_CODECS:
            res.add("flag", f"unexpected codec {res.codec}")
        if not res.width or not res.height:
            res.add("flag", "no frame dimensions")
        elif res.width < 16 or res.height < 16:
            res.add("fail", f"absurd dimensions {res.width}x{res.height}")
        elif res.width > 7680 or res.height > 4320:
            res.add("flag", f"oversized frame {res.width}x{res.height}")

    dur = fmt.get("duration") or (video or {}).get("duration")
    res.duration = float(dur) if dur else None
    max_dur = int(cfg["general"]["max_duration_s"]) + 5
    if res.duration and res.duration > max_dur:
        res.add("fail", f"duration {res.duration/60:.1f}m exceeds {max_dur/60:.0f}m cap")
    if not res.duration:
        res.add("flag", "unknown duration")

    if res.duration and res.duration > 0:
        mbps = res.size * 8 / res.duration / 1e6
        if mbps > float((cfg.get("safety") or {}).get("max_bitrate_mbps", 100)):
            res.add("flag", f"bitrate {mbps:.0f} Mb/s looks wrong")

    return res


# ---------------------------------------------------------------- quarantine
def quarantine(path: str, cfg, source: str, reason: str) -> str:
    qdir = os.path.join(config.path(cfg, "quarantine"), source or "unknown")
    os.makedirs(qdir, exist_ok=True)
    dst = os.path.join(qdir, os.path.basename(path))
    if os.path.exists(dst):
        base, ext = os.path.splitext(os.path.basename(path))
        dst = os.path.join(qdir, f"{base}-{int(os.path.getmtime(path))}{ext}")
    shutil.move(path, dst)
    return os.path.relpath(dst, config.ROOT)


def restore(path: str, cfg, source: str) -> str:
    """Move an approved file back into the collection tree."""
    vdir = os.path.join(config.path(cfg, "videos"), source or "unknown")
    os.makedirs(vdir, exist_ok=True)
    dst = os.path.join(vdir, os.path.basename(path))
    if os.path.exists(dst):
        base, ext = os.path.splitext(os.path.basename(path))
        dst = os.path.join(vdir, f"{base}-approved{ext}")
    shutil.move(path, dst)
    return os.path.relpath(dst, config.ROOT)


def frames(path: str, cfg, count: int = 3) -> list[str]:
    """Extract a contact sheet for human review. Returns absolute paths."""
    exe = which("ffmpeg")
    if not exe or not os.path.exists(path):
        return []
    src = path if os.path.isabs(path) else os.path.join(config.ROOT, path)
    outdir = os.path.join(config.ROOT, "data", "review",
                          os.path.splitext(os.path.basename(src))[0])
    os.makedirs(outdir, exist_ok=True)
    made = []
    for i, frac in enumerate((0.15, 0.5, 0.85), 1):
        out = os.path.join(outdir, f"frame{i}.jpg")
        p = subprocess.run(
            [exe, "-v", "error", "-y", "-ss", str(frac), "-i", src,
             "-frames:v", "1", "-vf", "scale=480:-1", out],
            capture_output=True, text=True, timeout=120)
        if p.returncode == 0 and os.path.exists(out):
            made.append(out)
    return made[:count]
