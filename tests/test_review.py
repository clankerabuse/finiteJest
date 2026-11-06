"""require_review gate tests — real media, real db, no network.

`safety.require_review = true` is documented as "every download waits for
approval". These tests drive pipeline.run_fetch with a stubbed download that
hands back a genuine mp4 and check where the row and the file end up.

    python3 tests/test_review.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fj import config, db as dbmod, pipeline, ytdlp

tmp = tempfile.mkdtemp(prefix="fj-review-")
GOOD = os.path.join(tmp, "clip.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                "-i", "testsrc=size=320x240:rate=15:duration=2",
                "-pix_fmt", "yuv420p", GOOD], check=True)


def _cfg(root, require_review):
    cfg = config.load()
    cfg["safety"]["require_review"] = require_review
    # keep every path inside the throwaway dir
    cfg["paths"] = {
        "videos": os.path.join(root, "videos"),
        "meta": os.path.join(root, "meta"),
        "quarantine": os.path.join(root, "quarantine"),
        "exports": os.path.join(root, "collection"),
        "db": os.path.join(root, "finitejest.db"),
    }
    return cfg


def _fetch_one(root, require_review):
    """Run one already-probed row through fetch. Returns (row, db)."""
    cfg = _cfg(root, require_review)
    d = dbmod.DB(config.path(cfg, "db"))
    vid, created = d.add({
        "source": "web",
        "source_url": "https://example.test/clip.mp4",
        "url_key": "example.test/clip.mp4",
        "title": "test clip",
        "status": "probed",
        "est_size": 4096,
        "duration": 2,
    })
    assert created, "fixture row was not inserted"

    def fake_download(url, dest_dir, source, fmt, **kw):
        outdir = os.path.join(dest_dir, source)
        os.makedirs(outdir, exist_ok=True)
        dst = os.path.join(outdir, "test-clip.mp4")
        shutil.copy(GOOD, dst)
        return ytdlp.DownloadResult(ok=True, path=dst)

    original = pipeline.ytdlp.download
    pipeline.ytdlp.download = fake_download
    try:
        pipeline.run_fetch(d, cfg, count=5)
    finally:
        pipeline.ytdlp.download = original
    return d.get(vid), cfg


try:
    # --- default: require_review off, a clean download joins the collection
    root = os.path.join(tmp, "open")
    row, cfg = _fetch_one(root, require_review=False)
    assert row["status"] == "done", (row["status"], row["error"])
    assert os.path.exists(os.path.join(config.ROOT, row["path"])), row["path"]

    # --- require_review on: nothing may reach `done` without `fj approve`
    root = os.path.join(tmp, "held")
    row, cfg = _fetch_one(root, require_review=True)
    assert row["status"] == "quarantined", (
        f"require_review=true let a download through as {row['status']!r}", row["error"])
    assert "require_review" in (row["error"] or ""), row["error"]
    held = os.path.join(config.ROOT, row["path"])
    assert os.path.exists(held), row["path"]
    qdir = config.path(cfg, "quarantine")
    assert os.path.commonpath([os.path.abspath(held), os.path.abspath(qdir)]) == os.path.abspath(qdir), held
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("review gate tests: ok")
