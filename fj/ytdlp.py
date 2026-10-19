"""Thin wrapper around the yt-dlp binary: probe, download, enumerate playlists."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

from .util import which

BIN = which("yt-dlp") or "yt-dlp"
TIMEOUT_PROBE = 120


class YtdlpError(RuntimeError):
    pass


def version() -> str:
    try:
        return subprocess.run([BIN, "--version"], capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception:
        return "missing"


def _base_args(cookies: str = "") -> list[str]:
    args = [BIN, "--no-warnings", "--no-playlist", "--socket-timeout", "20",
            "--ignore-config"]
    if cookies:
        args += ["--cookies-from-browser", cookies]
    return args


def probe(url: str, fmt: str | None = None, cookies: str = "", flat: bool = False,
          playlist_end: int | None = None) -> dict:
    args = _base_args(cookies)
    if flat:
        args += ["--flat-playlist", "--ignore-errors"]
    if playlist_end:
        args += ["--playlist-end", str(playlist_end)]
    if fmt:
        args += ["-f", fmt]
    args += ["-J", url]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_PROBE)
    except subprocess.TimeoutExpired:
        raise YtdlpError(f"probe timed out: {url}") from None
    if not p.stdout.strip():
        err = (p.stderr or "").strip().splitlines()
        raise YtdlpError(err[-1] if err else f"no output from yt-dlp for {url}")
    try:
        info = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise YtdlpError(f"unparseable yt-dlp output for {url}") from None
    if info is None:
        err = (p.stderr or "").strip().splitlines()
        raise YtdlpError(err[-1] if err else f"nothing extractable at {url}")
    if not flat and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise YtdlpError(f"empty playlist/collection: {url}")
        info = entries[0]
    return info


def estimate_size(info: dict) -> int | None:
    """Bytes for the format selection probe() asked for."""
    total = 0
    for f in info.get("requested_formats") or []:
        n = f.get("filesize") or f.get("filesize_approx")
        if n:
            total += int(n)
    if total:
        return total
    for k in ("filesize", "filesize_approx"):
        if info.get(k):
            return int(info[k])
    # direct-file extractors (generic) often omit size; ask the server
    if str(info.get("protocol") or "") in ("http", "https") and info.get("url"):
        from .util import head_filesize
        n = head_filesize(info["url"])
        if n:
            return int(n)
    return None


@dataclass
class DownloadResult:
    ok: bool
    path: str | None = None
    info_path: str | None = None
    code: int = 0
    stderr: str = ""
    stdout: str = ""
    too_large: bool = False
    log: list[str] = field(default_factory=list)


def download(url: str, dest_dir: str, source: str, fmt: str, merge: str = "mp4",
             max_bytes: int | None = None, cookies: str = "",
             thumbs: bool = False, extra: list[str] | None = None) -> DownloadResult:
    os.makedirs(dest_dir, exist_ok=True)
    outdir = os.path.join(dest_dir, source)
    os.makedirs(outdir, exist_ok=True)
    template = os.path.join(
        outdir, "%(upload_date>%Y-%m-%d)s-%(title).80s-%(id)s.%(ext)s")
    args = _base_args(cookies) + [
        "--no-simulate", "--newline", "--continue", "--no-mtime",
        "--trim-filenames", "140",
        "-f", fmt, "--merge-output-format", merge,
        "--write-info-json",
        "--print", "after_move:filepath",
        "-o", template,
    ]
    if max_bytes:
        args += ["--max-filesize", str(int(max_bytes))]
    if thumbs:
        args += ["--write-thumbnail"]
        if which("ffmpeg"):
            args += ["--convert-thumbnails", "jpg"]
    if extra:
        args += extra
    args.append(url)

    p = subprocess.run(args, capture_output=True, text=True, timeout=3600 * 3)
    stderr = p.stderr or ""
    too_large = "max-filesize" in stderr or "larger than max-filesize" in stderr
    media = None
    for line in reversed([l.strip() for l in (p.stdout or "").splitlines() if l.strip()]):
        if os.path.isfile(line):
            media = line
            break
    if media is None:
        # merged downloads sometimes print the pre-merge name; fall back to newest file
        candidates = [os.path.join(outdir, f) for f in os.listdir(outdir)
                      if not f.endswith((".part", ".json", ".jpg", ".webp", ".ytdl"))]
        if candidates:
            media = max(candidates, key=os.path.getmtime)
    info_path = None
    if media:
        guess = os.path.splitext(media)[0] + ".info.json"
        if os.path.exists(guess):
            info_path = guess
    return DownloadResult(ok=(p.returncode == 0 and media is not None), path=media,
                          info_path=info_path, code=p.returncode, stderr=stderr,
                          stdout=p.stdout or "", too_large=too_large,
                          log=[l for l in stderr.splitlines() if l.strip()][-5:])


def flat_entries(info: dict) -> list[dict]:
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if not str(url).startswith("http"):
            vid = e.get("id")
            if e.get("ie_key", "").lower().startswith("youtube") and vid:
                url = f"https://www.youtube.com/watch?v={vid}"
        if str(url).startswith("http"):
            out.append({
                "url": url,
                "title": e.get("title"),
                "source_id": e.get("id"),
                "duration": e.get("duration"),
                "uploader": e.get("uploader") or e.get("channel"),
                "score": e.get("view_count"),
            })
    return out
