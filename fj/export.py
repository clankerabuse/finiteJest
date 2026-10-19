"""Export the catalog (committed to git) from the local DB."""
from __future__ import annotations

import json
import os

from . import config
from .util import fmt_duration, human_size, now_iso


def _rows(db):
    return db.rows(status="done", order="id ASC")


def export(db, cfg, fmt: str = "both", out: str | None = None) -> list[str]:
    rows = _rows(db)
    outdir = out or config.path(cfg, "exports")
    os.makedirs(outdir, exist_ok=True)
    written = []

    payload = {
        "generated_at": now_iso(),
        "count": len(rows),
        "bytes": sum(int(r["filesize"] or 0) for r in rows),
        "items": [{
            "id": r["id"],
            "title": r["title"],
            "source": r["source"],
            "source_url": r["source_url"],
            "uploader": r["uploader"],
            "duration": r["duration"],
            "published": r["published"],
            "tags": r["tags"],
            "score": r["score"],
            "path": r["path"],
            "filesize": r["filesize"],
            "sha256": r["sha256"],
            "width": r["width"],
            "height": r["height"],
            "note": r["note"],
            "fetched_at": r["fetched_at"],
        } for r in rows],
    }

    if fmt in ("json", "both"):
        p = os.path.join(outdir, "index.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        written.append(p)

    if fmt in ("md", "both"):
        lines = [
            "# finiteJest",
            "",
            f"{len(rows)} videos · {human_size(payload['bytes'])} · "
            f"generated {payload['generated_at'][:10]}",
            "",
        ]
        for i, r in enumerate(rows, 1):
            title = (r["title"] or "untitled").replace("[", "(").replace("]", ")")
            bits = [fmt_duration(r["duration"]), human_size(r["filesize"]), r["source"]]
            if r["tags"]:
                bits.append(r["tags"])
            lines.append(f"{i}. [{title}]({r['source_url']}) — " + " · ".join(bits))
        lines.append("")
        p = os.path.join(outdir, "index.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        written.append(p)

    return written
