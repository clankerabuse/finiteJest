"""finiteJest CLI: harvest -> add -> probe -> fetch -> export."""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from . import config, db as dbmod, export as exportmod, pipeline, safety, sources, ytdlp
from .util import canonical_key, fmt_duration, human_size, now_iso, parse_size, slug


def _cfg(args):
    return config.load(getattr(args, "config", None))


def _db(cfg):
    return dbmod.DB(config.path(cfg, "db"))


def _banner(cfg, db):
    c = db.counts()
    used = db.bytes_done()
    budget = int(cfg["general"]["budget_gib"] * 1024 ** 3)
    extra = ""
    if c.get("quarantined"):
        extra += f" · {c['quarantined']} QUARANTINED"
    if c.get("blocked"):
        extra += f" · {c['blocked']} blocked"
    print(f"finiteJest · {c.get('done', 0)}/{cfg['general']['target_count']} videos · "
          f"{human_size(used)}/{human_size(budget)} · "
          f"{c.get('candidate', 0)} candidates · {c.get('probed', 0)} ready · "
          f"{c.get('failed', 0)} failed" + extra)


# ------------------------------------------------------------------ commands
def cmd_init(args):
    cfg = _cfg(args)
    db = _db(cfg)
    for key in ("videos", "meta", "quarantine", "exports"):
        os.makedirs(config.path(cfg, key), exist_ok=True)
    _banner(cfg, db)
    print("yt-dlp:", ytdlp.version())
    print("videos:", config.path(cfg, "videos"))
    print("db:    ", config.path(cfg, "db"))


def _add_rows(db, rows, probes=False, cfg=None, tag=None, verbose=True):
    added = dup = blocked = 0
    ids = []
    for r in rows:
        url = r.get("url")
        if not url:
            continue
        r = dict(r)
        r.setdefault("source", "web")
        r["url_key"] = canonical_key(url)
        tags = [t for t in str(r.get("tags") or "").split(",") if t.strip()]
        if tag:
            tags.append(tag)
        r["tags"] = ",".join(dict.fromkeys(t.strip() for t in tags if t.strip()))
        if cfg is not None:
            tv, hits = safety.screen_text(cfg, r.get("title"), r.get("tags"), r.get("note"))
            if tv == "blocked":
                r["status"] = "blocked"
                r["error"] = "text: " + ", ".join(hits)
                blocked += 1
            elif tv == "review":
                r["note"] = ((r.get("note") or "") + " · review: " + ", ".join(hits)).strip(" ·")
        vid, created = db.add(r)
        if created:
            added += 1
            ids.append(vid)
        else:
            dup += 1
    if verbose:
        msg = f"added {added}, {dup} already known"
        if blocked:
            msg += f", {blocked} blocked by policy"
        print(msg)
    if probes and cfg:
        for vid in ids:
            row = db.get(vid)
            if row:
                pipeline.probe_one(db, row, cfg)
    return added, dup


def cmd_add(args):
    cfg = _cfg(args)
    db = _db(cfg)
    rows = []
    for url in args.urls or []:
        rows.append({"url": url, "source": args.source, "title": None})
    if args.file:
        import urllib.parse
        with open(args.file, encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if u and not u.startswith("#") and urllib.parse.urlsplit(u).scheme in ("http", "https"):
                    rows.append({"url": u, "source": args.source})
    if not rows:
        _banner(cfg, db)
        return
    _add_rows(db, rows, probes=args.probe, cfg=cfg, tag=args.tag)
    _banner(cfg, db)


def cmd_harvest(args):
    cfg = _cfg(args)
    db = _db(cfg)
    src = sources.get(args.source)
    kw = {}
    if args.source == "reddit":
        subs = [args.query] if args.query else cfg["reddit"].get("subreddits", [])
        if not subs:
            sys.exit("need a subreddit (or set [reddit].subreddits in config.toml)")
        rows = []
        for s in subs:
            print(f"harvesting r/{s} · {args.sort}/{args.timeframe} · limit {args.limit}")
            try:
                rows += src.harvest(cfg, s, sort=args.sort, timeframe=args.timeframe,
                                    limit=args.limit, min_score=args.min_score)
            except Exception as e:
                print(f"  ! r/{s}: {e}")
    elif args.source == "archive":
        print(f"harvesting archive.org · {args.query or args.collection or 'mediatype:movies'}")
        rows = src.harvest(cfg, query=args.query, collection=args.collection,
                           limit=args.limit, sort=args.sort_archive)
    elif args.source == "playlist":
        if not args.query:
            sys.exit("need a channel/playlist/search URL")
        print(f"harvesting playlist · {args.query}")
        rows = src.harvest(cfg, args.query, limit=args.limit, tag=args.tag)
    elif args.source == "fourchan":
        board = args.query or (cfg.get("fourchan", {}).get("boards") or ["wsg"])[0]
        print(f"harvesting /{board}/ · {args.threads} threads · filter {args.filter or 'none'}")
        rows = src.harvest(cfg, board=board, filter=args.filter,
                           limit=args.limit, threads=args.threads)
    elif args.source == "booru":
        if not args.query:
            sys.exit("need a booru base URL, e.g. https://e621.net")
        print(f"harvesting booru · {args.query} · tags {args.tags or '(none)'}")
        rows = src.harvest(cfg, args.query, tags=args.tags or "",
                           limit=args.limit, page=args.page or 0)
    elif args.source == "list":
        if not args.query:
            sys.exit("need a file path")
        rows = src.harvest(cfg, args.query, tag=args.tag)
    else:
        sys.exit(f"unknown source: {args.source}")

    print(f"found {len(rows)} candidate links")
    _add_rows(db, rows, cfg=cfg, tag=args.tag)
    _banner(cfg, db)


def cmd_probe(args):
    cfg = _cfg(args)
    if getattr(args, "max_duration", None):
        cfg["general"]["max_duration_s"] = args.max_duration
    db = _db(cfg)
    kept = pipeline.run_probe(db, cfg, limit=args.limit)
    print(f"\n{kept} probed")
    _banner(cfg, db)


def cmd_fetch(args):
    cfg = _cfg(args)
    if getattr(args, "max_duration", None):
        cfg["general"]["max_duration_s"] = args.max_duration
    db = _db(cfg)
    _banner(cfg, db)
    pipeline.run_fetch(db, cfg, limit=args.limit,
                       budget=parse_size(args.budget) if args.budget else None,
                       count=args.count, dry_run=args.dry_run,
                       thumbs=None if args.thumbs is None else args.thumbs,
                       ids=args.id or None)
    if not args.dry_run:
        for p in exportmod.export(db, cfg):
            print("wrote", os.path.relpath(p, config.ROOT))


def cmd_ls(args):
    cfg = _cfg(args)
    db = _db(cfg)
    rows = db.rows(status=args.status, limit=args.limit,
                   order="id ASC" if args.order == "id" else "COALESCE(score,0) DESC, id ASC")
    if args.json:
        import json
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
        return
    for r in rows:
        title = (r["title"] or r["source_url"])[:64]
        print(f"{r['id']:>4}  {r['status']:<10} {human_size(r['filesize']):>9} "
              f"{fmt_duration(r['duration']):>7}  {r['source']:<8} {title}")
        if r["error"] and args.errors:
            print(f"      └ {r['error'][:110]}")
    print(f"\n{len(rows)} rows")


def cmd_show(args):
    cfg = _cfg(args)
    db = _db(cfg)
    r = db.get(args.id)
    if not r:
        sys.exit(f"no row #{args.id}")
    for k in r.keys():
        v = r[k]
        if k in ("description",) and v:
            v = v[:400].replace("\n", " ")
        print(f"{k:>12}: {v}")


def cmd_rm(args):
    cfg = _cfg(args)
    db = _db(cfg)
    r = db.get(args.id)
    if not r:
        sys.exit(f"no row #{args.id}")
    paths = db.remove(args.id)
    if args.purge:
        for p in paths:
            fp = p if os.path.isabs(p) else os.path.join(config.ROOT, p)
            if os.path.exists(fp):
                os.unlink(fp)
                print("deleted", p)
    print(f"removed #{args.id}")


def cmd_requeue(args):
    cfg = _cfg(args)
    db = _db(cfg)
    n = db.requeue(args.status)
    print(f"requeued {n} rows from status={args.status}")
    _banner(cfg, db)


def cmd_verify(args):
    cfg = _cfg(args)
    db = _db(cfg)
    missing = mismatch = ok = 0
    for r in db.rows(status="done", order="id ASC"):
        if not r["path"]:
            continue
        fp = r["path"] if os.path.isabs(r["path"]) else os.path.join(config.ROOT, r["path"])
        if not os.path.exists(fp):
            db.update(r["id"], status="missing", error="file gone")
            print(f"  missing #{r['id']} {r['path']}")
            missing += 1
            continue
        size = os.path.getsize(fp)
        if r["filesize"] and size != r["filesize"]:
            print(f"  size changed #{r['id']} {human_size(r['filesize'])} -> {human_size(size)}")
            db.update(r["id"], filesize=size)
            mismatch += 1
        ok += 1
    print(f"{ok} ok · {missing} missing · {mismatch} size changes")


def cmd_stats(args):
    cfg = _cfg(args)
    db = _db(cfg)
    _banner(cfg, db)
    used = db.bytes_done()
    budget = int(cfg["general"]["budget_gib"] * 1024 ** 3)
    hours = db.total_duration() / 3600
    print(f"\nrun time: {hours:.1f} h of video")
    print(f"disk:     {human_size(used)} / {human_size(budget)} "
          f"({100 * used / budget:.0f}% of budget)")
    print("sources:")
    for r in db.by_source():
        print(f"  {r['source']:<10} {r['n']:>4} videos  {human_size(r['bytes']):>10}")
    print("statuses:", ", ".join(f"{k}={v}" for k, v in sorted(db.counts().items())))


def cmd_export(args):
    cfg = _cfg(args)
    db = _db(cfg)
    for p in exportmod.export(db, cfg, fmt=args.format, out=args.out):
        print("wrote", p)


def cmd_sources(args):
    cfg = _cfg(args)
    print("adapters:")
    print("  reddit    r/<sub> top/hot/new       needs a free reddit script app for OAuth")
    print("  archive   archive.org advancedsearch")
    print("  playlist  any yt-dlp channel/playlist/search URL (flat)")
    print("  fourchan  /<board>/ webm+mp4 posts  (default wsG, 1 req/sec)")
    print("  booru     gelbooru/danbooru/e621 API by base URL")
    print("  list      text file of URLs")
    print("\nreddit creds:", "set" if cfg["reddit"]["client_id"] else "missing (see README)")
    print("yt-dlp:", ytdlp.version())


def cmd_scan(args):
    """Re-run the byte scanner over files already on disk."""
    cfg = _cfg(args)
    db = _db(cfg)
    rows = db.rows(status=args.status, order="id ASC")
    verdicts = {"ok": 0, "flag": 0, "fail": 0}
    for r in rows:
        if not r["path"]:
            continue
        p = r["path"] if os.path.isabs(r["path"]) else os.path.join(config.ROOT, r["path"])
        if not os.path.exists(p):
            db.update(r["id"], status="missing", error="file gone")
            print(f"  missing #{r['id']} {r['path']}")
            continue
        res = safety.scan(p, cfg)
        verdicts[res.verdict] += 1
        if res.verdict != "ok" or args.verbose:
            print(f"  {res.verdict:<4} #{r['id']:<5} {human_size(res.size):>9}  {res.reason[:90]}")
        if res.verdict != "ok" and args.quarantine:
            q = safety.quarantine(p, cfg, r["source"], res.reason)
            db.update(r["id"], status="quarantined", path=q,
                      error=f"{res.verdict}: {res.reason}")
            print(f"        -> quarantined {q}")
    print(f"\n{verdicts['ok']} clean · {verdicts['flag']} flagged · {verdicts['fail']} failed")


def cmd_review(args):
    """Show what is waiting for a human, with optional frames for eyeballing."""
    cfg = _cfg(args)
    db = _db(cfg)
    rows = db.rows(status="quarantined", order="id ASC")
    if not rows:
        print("quarantine empty")
        return
    for r in rows:
        print(f"#{r['id']:<5} {human_size(r['filesize']):>9}  {r['source']:<9} "
              f"{(r['title'] or '')[:50]}")
        print(f"       {r['error'] or 'no reason recorded'}")
        print(f"       {r['path']}")
        print(f"       {r['source_url']}")
        if args.frames:
            for f in safety.frames(r["path"], cfg):
                print(f"       frame: {f}")
    print(f"\n{len(rows)} awaiting review · approve with `bin/fj approve <id>`, "
          f"discard with `bin/fj reject <id>`")


def cmd_approve(args):
    cfg = _cfg(args)
    db = _db(cfg)
    r = db.get(args.id)
    if not r:
        sys.exit(f"no row #{args.id}")
    if r["status"] != "quarantined":
        sys.exit(f"#{args.id} is {r['status']}, not quarantined")
    src = r["path"] if os.path.isabs(r["path"]) else os.path.join(config.ROOT, r["path"])
    if not os.path.exists(src):
        sys.exit(f"file missing: {r['path']}")
    rel = safety.restore(src, cfg, r["source"])
    db.update(r["id"], status="done", path=rel, error=None,
              fetched_at=r["fetched_at"] or now_iso())
    print(f"approved #{args.id} -> {rel}")
    for p in exportmod.export(db, cfg):
        print("wrote", os.path.relpath(p, config.ROOT))


def cmd_reject(args):
    cfg = _cfg(args)
    db = _db(cfg)
    r = db.get(args.id)
    if not r:
        sys.exit(f"no row #{args.id}")
    p = r["path"] if os.path.isabs(r["path"]) else os.path.join(config.ROOT, r["path"] or "")
    if args.purge and p and os.path.exists(p):
        os.unlink(p)
        print("deleted", r["path"])
    db.update(r["id"], status="blocked", error=(r["error"] or "") + " · rejected by reviewer")
    print(f"rejected #{args.id}")


# ------------------------------------------------------------------ parser
def build_parser():
    p = argparse.ArgumentParser(prog="fj", description="finiteJest video farm")
    p.add_argument("--config", help="alternate config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create dirs/db")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("add", help="add URL(s) to the queue")
    s.add_argument("urls", nargs="*")
    s.add_argument("--file", help="read URLs from a file")
    s.add_argument("--source", default="web")
    s.add_argument("--tag")
    s.add_argument("--probe", action="store_true", help="probe immediately")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("harvest", help="pull candidate URLs from a source")
    s.add_argument("source", choices=list(sources.ADAPTERS))
    s.add_argument("query", nargs="?", help="subreddit / archive query / playlist URL / file")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--tag")
    s.add_argument("--sort", default="top", help="reddit sort: top|hot|new")
    s.add_argument("--timeframe", default="all", help="top timeframe: all|year|month|week")
    s.add_argument("--min-score", type=int, default=None)
    s.add_argument("--collection", help="archive.org collection id")
    s.add_argument("--sort-archive", default="downloads",
                   help="archive sort: downloads|recent|oldest|random")
    s.add_argument("--filter", help="fourchan: thread subject substring")
    s.add_argument("--threads", type=int, default=12, help="fourchan: threads to scan")
    s.add_argument("--tags", help="booru: search tags")
    s.add_argument("--page", type=int, default=0, help="booru: page offset")
    s.set_defaults(fn=cmd_harvest)

    s = sub.add_parser("probe", help="enrich + quality-gate candidates")
    s.add_argument("--limit", type=int)
    s.add_argument("--max-duration", type=int, help="override the 5m cap (seconds)")
    s.set_defaults(fn=cmd_probe)

    s = sub.add_parser("fetch", help="download until budget/count is reached")
    s.add_argument("--limit", type=int, help="max rows to consider this run")
    s.add_argument("--id", type=int, action="append", help="fetch specific row(s) by id")
    s.add_argument("--count", type=int, help="target number of videos")
    s.add_argument("--budget", help="e.g. 15G")
    s.add_argument("--max-duration", type=int, help="override the 5m cap (seconds)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--thumbs", dest="thumbs", action="store_true", default=None)
    s.add_argument("--no-thumbs", dest="thumbs", action="store_false")
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("ls", help="list rows")
    s.add_argument("--status")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--order", choices=["id", "score"], default="id")
    s.add_argument("--json", action="store_true")
    s.add_argument("-e", "--errors", action="store_true", help="show rejection reasons")
    s.set_defaults(fn=cmd_ls)

    s = sub.add_parser("show", help="dump one row")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("rm", help="remove a row")
    s.add_argument("id", type=int)
    s.add_argument("--purge", action="store_true", help="delete the file too")
    s.set_defaults(fn=cmd_rm)

    s = sub.add_parser("requeue", help="reset rows to candidate")
    s.add_argument("--status", default="failed")
    s.set_defaults(fn=cmd_requeue)

    s = sub.add_parser("verify", help="check files on disk")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("stats", help="counts, bytes, sources")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("export", help="write collection/index.{json,md}")
    s.add_argument("--format", choices=["json", "md", "both"], default="both")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("sources", help="show adapters and cred status")
    s.set_defaults(fn=cmd_sources)

    s = sub.add_parser("scan", help="re-scan files already on disk")
    s.add_argument("--status", default="done", help="row status to scan (default done)")
    s.add_argument("--quarantine", action="store_true", help="move failures to quarantine")
    s.add_argument("-v", "--verbose", action="store_true", help="print clean files too")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("review", help="list quarantined files for human review")
    s.add_argument("--frames", action="store_true", help="extract 3 review frames per file")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("approve", help="promote a quarantined file into the collection")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="discard a quarantined row")
    s.add_argument("id", type=int)
    s.add_argument("--purge", action="store_true", help="delete the file too")
    s.set_defaults(fn=cmd_reject)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
