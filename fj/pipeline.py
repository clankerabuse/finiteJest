"""The actual pipeline: probe -> quality gate -> download -> verify -> record."""
from __future__ import annotations

import os
import time
import urllib.parse

from . import config, safety, ytdlp
from .util import gib, human_size, now_iso

AUDIO_EXT = {"m4a", "mp3", "opus", "ogg", "flac", "wav", "aac"}
BAD_AVAILABILITY = {"private", "premium_only", "subscriber_only", "needs_auth",
                    "unlisted", "no_source"}


def gate(info: dict, cfg: dict) -> tuple[str, str | None, int | None]:
    """Decide whether this video is worth/wanted. Returns (verdict, reason, est_bytes).

    verdict: keep | reject
    """
    est = ytdlp.estimate_size(info)
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "reject", "live stream", est
    if str(info.get("availability") or "").lower() in BAD_AVAILABILITY:
        return "reject", f"unavailable ({info.get('availability')})", est
    if info.get("_has_drm") or info.get("has_drm"):
        return "reject", "DRM", est

    dur = info.get("duration")
    if dur is not None:
        if dur < cfg["general"]["min_duration_s"]:
            return "reject", f"too short ({dur:.0f}s)", est
        if dur > cfg["general"]["max_duration_s"]:
            return "reject", f"too long ({dur/60:.0f}m)", est

    ext = (info.get("ext") or "").lower()
    if ext in AUDIO_EXT or str(info.get("vcodec") or "").lower() == "none":
        return "reject", "audio only", est

    limit = int(cfg["video"]["max_file_mib"]) * 1024 ** 2
    if est and est > limit:
        return "reject", f"too big (~{human_size(est)})", est
    return "keep", None, est


def enrich(row, info: dict, cfg: dict, verdict: str, reason: str | None,
           est: int | None) -> dict:
    widths = [f.get("width") for f in (info.get("requested_formats") or []) if f.get("width")]
    heights = [f.get("height") for f in (info.get("requested_formats") or []) if f.get("height")]
    desc = (info.get("description") or "")[:4000]

    # direct-file extractors call every video by its filename (often an md5 or
    # a 4chan tim); keep the human title the adapter already gave us
    probe_title = (info.get("title") or "").strip()
    url_base = os.path.splitext(os.path.basename(
        urllib.parse.urlsplit(row["source_url"]).path))[0]
    if row["title"] and (not probe_title or probe_title == url_base
                         or len(probe_title) < 3):
        title = row["title"]
    else:
        title = probe_title or row["title"]

    return {
        "title": title,
        "uploader": info.get("uploader") or info.get("channel") or row["uploader"],
        "duration": info.get("duration") or row["duration"],
        "published": info.get("upload_date") or row["published"],
        "description": desc or row["description"],
        "ext": info.get("ext") or row["ext"],
        "width": (max(widths) if widths else info.get("width")) or row["width"],
        "height": (max(heights) if heights else info.get("height")) or row["height"],
        "est_size": est,
        "status": "rejected" if verdict == "reject" else "probed",
        "error": reason if verdict == "reject" else None,
    }


def probe_one(db, row, cfg, verbose=True) -> dict | None:
    """Probe a single row and write metadata + gate verdict. Returns info dict."""
    src = row["source"]
    try:
        info = ytdlp.probe(row["source_url"], fmt=cfg["video"]["format"],
                           cookies=cfg["video"].get("cookies_from_browser", ""))
    except Exception as e:
        db.update(row["id"], status="failed", attempts=(row["attempts"] or 0) + 1,
                  error=f"probe: {e}")
        if verbose:
            print(f"  probe failed  #{row['id']}  {e}")
        return None
    verdict, reason, est = gate(info, cfg)
    db.update(row["id"], **enrich(row, info, cfg, verdict, reason, est))
    # second look with the metadata we just learned (tags/title often carry the
    # only signal a script can act on)
    if row["tags"] or info.get("title"):
        tv, hits = safety.screen_text(cfg, info.get("title"), row["tags"], row["note"])
        if tv == "blocked":
            db.update(row["id"], status="blocked", error="text: " + ", ".join(hits))
            if verbose:
                print(f"  BLOCK #{row['id']}  text: {', '.join(hits)[:80]}")
            return info
        if tv == "review":
            db.update(row["id"], note=(row["note"] or "") + " · review: " + ", ".join(hits))
    if verbose:
        mark = "ok " if verdict == "keep" else "skip"
        extra = f" — {reason}" if reason else ""
        print(f"  {mark} #{row['id']:<5} {human_size(est):>9}  "
              f"{(info.get('title') or row['title'] or '?')[:58]}{extra}")
    return info


def run_probe(db, cfg, limit=None) -> int:
    rows = [r for r in db.rows(status="candidate", limit=limit,
                               order="COALESCE(score,0) DESC, id ASC")]
    kept = 0
    for r in rows:
        if probe_one(db, r, cfg) is not None:
            kept += 1
    return kept


def run_fetch(db, cfg, limit: int | None = None, budget: int | None = None,
              count: int | None = None, dry_run: bool = False,
              thumbs: bool | None = None, ids: list[int] | None = None) -> dict:
    budget = budget or gib(cfg["general"]["budget_gib"])
    target = count or int(cfg["general"]["target_count"])
    used = db.bytes_done()
    done = db.count_done()
    thumbs = cfg["video"]["thumbs"] if thumbs is None else thumbs
    videos_dir = config.path(cfg, "videos")
    meta_dir = config.path(cfg, "meta")
    max_file = int(cfg["video"]["max_file_mib"]) * 1024 ** 2
    fmt = cfg["video"]["format"]
    merge = cfg["video"]["merge"]
    cookies = cfg["video"].get("cookies_from_browser", "")

    rows = ([(r) for r in (db.get(i) for i in ids) if r] if ids else
            db.due(limit=limit, max_attempts=int(cfg["general"]["max_attempts"])))
    started = time.time()
    stats = {"done": 0, "rejected": 0, "skipped": 0, "failed": 0, "bytes": 0,
             "quarantined": 0}

    for row in rows:
        if done >= target:
            print(f"\nreached target of {target} videos.")
            break
        remaining = budget - used
        if remaining <= min(64 * 1024 ** 2, budget * 0.02):
            print(f"\nbudget nearly full ({human_size(used)} / {human_size(budget)}).")
            break

        if dry_run:
            info = None
            try:
                info = ytdlp.probe(row["source_url"], fmt=fmt, cookies=cookies)
            except Exception as e:
                print(f"  ?  #{row['id']} probe failed: {e}")
                continue
            verdict, reason, est = gate(info, cfg)
            tag = "WOULD GET" if verdict == "keep" else "SKIP"
            print(f"  {tag:<9} {human_size(est):>9}  {(info.get('title') or '?')[:60]}"
                  + (f"  — {reason}" if reason else ""))
            continue

        # --- metadata
        if row["status"] == "candidate" or not row["est_size"]:
            info = probe_one(db, row, cfg)
            if info is None:
                stats["failed"] += 1
                continue
            row = db.get(row["id"])
            if row["status"] == "rejected":
                stats["rejected"] += 1
                continue
        est = row["est_size"] or 0

        # --- budget pre-check
        if est and est > remaining:
            db.update(row["id"], status="skipped",
                      error=f"budget: needs {human_size(est)}, {human_size(remaining)} left")
            print(f"  skip  #{row['id']}  needs {human_size(est)} > {human_size(remaining)} left")
            stats["skipped"] += 1
            continue
        if est > max_file:
            db.update(row["id"], status="rejected", error=f"too big (~{human_size(est)})")
            stats["rejected"] += 1
            continue

        # --- download
        db.update(row["id"], status="queued")
        title = (row["title"] or row["source_url"])[:60]
        print(f"  ...   #{row['id']}  {human_size(est) if est else '?':>9}  {title}")
        res = ytdlp.download(row["source_url"], videos_dir, row["source"], fmt,
                             merge=merge, max_bytes=min(remaining, max_file),
                             cookies=cookies, thumbs=thumbs)
        if not res.ok or not res.path:
            attempts = (row["attempts"] or 0) + 1
            status = "failed" if attempts < int(cfg["general"]["max_attempts"]) else "dead"
            db.update(row["id"], status=status, attempts=attempts,
                      error=(f"size limit: {res.stderr[-300:]}" if res.too_large
                             else (res.stderr or "download failed")[-300:]))
            print(f"  FAIL  #{row['id']}  {res.stderr.strip().splitlines()[-1][:90] if res.stderr.strip() else 'no file'}")
            stats["failed"] += 1
            continue

        size = os.path.getsize(res.path)
        if size > max_file or size > remaining:
            os.unlink(res.path)
            why = "too big" if size > max_file else "over budget"
            db.update(row["id"], status="skipped" if why == "over budget" else "rejected",
                      error=f"{why} after download ({human_size(size)})")
            print(f"  drop  #{row['id']}  {human_size(size)} ({why})")
            stats["skipped"] += 1
            continue

        # --- screen the bytes before anything is allowed into the collection
        scan = safety.scan(res.path, cfg)
        verdict = scan.verdict
        hits: list[str] = []
        if verdict != "fail":
            tverdict, hits = safety.screen_text(cfg, row["title"], row["tags"], row["note"])
            if tverdict == "blocked":
                verdict = "fail"
                scan.findings.append("text: " + ", ".join(hits))
            elif tverdict == "review":
                verdict = "flag" if verdict == "ok" else verdict
                scan.findings.append("text: " + ", ".join(hits))

        if verdict != "ok":
            qpath = safety.quarantine(res.path, cfg, row["source"], scan.reason)
            db.update(row["id"], status="quarantined", path=qpath, sha256=scan.sha256,
                      filesize=scan.size, duration=scan.duration or row["duration"],
                      width=scan.width, height=scan.height,
                      error=f"{verdict}: {scan.reason}")
            label = "QUARANTINE" if verdict == "fail" else "review"
            print(f"  {label}  #{row['id']}  {scan.reason[:90]}")
            stats["quarantined"] += 1
            continue

        digest = scan.sha256
        dup = db.conn.execute(
            "SELECT id FROM videos WHERE sha256 = ? AND id != ? AND status='done'",
            (digest, row["id"])).fetchone()
        if dup:
            os.unlink(res.path)
            db.update(row["id"], status="duplicate", sha256=digest,
                      error=f"same bytes as #{dup['id']}")
            print(f"  dup   #{row['id']}  identical to #{dup['id']}")
            stats["skipped"] += 1
            continue

        rel = os.path.relpath(res.path, config.ROOT)
        if res.info_path and os.path.exists(res.info_path):
            os.makedirs(meta_dir, exist_ok=True)
            dst = os.path.join(meta_dir, os.path.basename(res.info_path))
            try:
                os.replace(res.info_path, dst)
            except OSError:
                pass

        db.update(row["id"], status="done", path=rel, filesize=size, sha256=digest,
                  ext=os.path.splitext(res.path)[1].lstrip("."),
                  duration=scan.duration or row["duration"],
                  width=scan.width or row["width"],
                  height=scan.height or row["height"],
                  fetched_at=now_iso(), error=None)
        used += size
        done += 1
        stats["done"] += 1
        stats["bytes"] += size
        print(f"  ok    #{row['id']}  {human_size(size):>9}  "
              f"[{done}/{target}] {human_size(used)}/{human_size(budget)}  {title}")

    if not dry_run:
        mins = (time.time() - started) / 60
        print(f"\n{stats['done']} saved · {stats['rejected']} rejected · "
              f"{stats['skipped']} skipped · {stats['failed']} failed · "
              f"{stats['quarantined']} quarantined · "
              f"{human_size(stats['bytes'])} in {mins:.1f} min")
        print(f"collection: {done} videos, {human_size(used)} / {human_size(budget)}")
    return stats
