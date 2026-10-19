"""SQLite manifest. Rows are videos; the file on disk is just a cache of them."""
from __future__ import annotations

import os
import sqlite3

from .util import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT,
    source_url    TEXT NOT NULL,
    media_url     TEXT,
    url_key       TEXT NOT NULL UNIQUE,
    title         TEXT,
    uploader      TEXT,
    duration      REAL,
    published     TEXT,
    description   TEXT,
    tags          TEXT DEFAULT '',
    score         INTEGER,
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'candidate',
    attempts      INTEGER NOT NULL DEFAULT 0,
    filesize      INTEGER,
    est_size      INTEGER,
    width         INTEGER,
    height        INTEGER,
    ext           TEXT,
    path          TEXT,
    sha256        TEXT,
    error         TEXT,
    added_at      TEXT NOT NULL,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_sha    ON videos(sha256);
CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source);
"""

# fields a caller may set on add()/update()
FIELDS = ("source", "source_id", "source_url", "media_url", "url_key", "title",
          "uploader", "duration", "published", "description", "tags", "score",
          "note", "status", "attempts", "filesize", "est_size", "width",
          "height", "ext", "path", "sha256", "error", "fetched_at")


class DB:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ writes
    def add(self, row: dict) -> tuple[int, bool]:
        """Insert a candidate. Returns (id, created). Dedupes on url_key."""
        row = dict(row)
        if not row.get("source_url") and row.get("url"):
            row["source_url"] = row["url"]
        if not row.get("url_key") and row.get("source_url"):
            from .util import canonical_key
            row["url_key"] = canonical_key(row["source_url"])
        data = {k: row.get(k) for k in FIELDS if k in row}
        data.setdefault("status", "candidate")
        data["added_at"] = now_iso()
        cols = ", ".join(data)
        ph = ", ".join("?" for _ in data)
        try:
            cur = self.conn.execute(f"INSERT INTO videos ({cols}) VALUES ({ph})",
                                    tuple(data.values()))
            self.conn.commit()
            return int(cur.lastrowid), True
        except sqlite3.IntegrityError as e:
            if "url_key" not in str(e):
                raise
            r = self.conn.execute("SELECT id FROM videos WHERE url_key = ?",
                                  (data.get("url_key"),)).fetchone()
            return (int(r["id"]) if r else 0), False

    def update(self, vid: int, **fields) -> None:
        fields = {k: v for k, v in fields.items() if k in FIELDS}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE videos SET {sets} WHERE id = ?",
                          (*fields.values(), vid))
        self.conn.commit()

    def remove(self, vid: int) -> list[str]:
        r = self.get(vid)
        paths = []
        if r:
            for k in ("path",):
                if r[k]:
                    paths.append(r[k])
        self.conn.execute("DELETE FROM videos WHERE id = ?", (vid,))
        self.conn.commit()
        return paths

    # ------------------------------------------------------------ reads
    def get(self, vid: int):
        return self.conn.execute("SELECT * FROM videos WHERE id = ?", (vid,)).fetchone()

    def rows(self, status: str | None = None, limit: int | None = None,
             order: str = "COALESCE(score,0) DESC, id ASC"):
        q = "SELECT * FROM videos"
        args = []
        if status:
            if "," in status:
                marks = ",".join("?" for _ in status.split(","))
                q += f" WHERE status IN ({marks})"
                args += [s.strip() for s in status.split(",")]
            else:
                q += " WHERE status = ?"
                args.append(status)
        q += f" ORDER BY {order}"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.conn.execute(q, args).fetchall()

    def due(self, limit: int | None = None, max_attempts: int = 3, statuses=("candidate", "probed", "failed")):
        marks = ",".join("?" for _ in statuses)
        q = (f"SELECT * FROM videos WHERE status IN ({marks}) AND attempts < ? "
             f"ORDER BY COALESCE(score,0) DESC, id ASC")
        args = list(statuses) + [max_attempts]
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.conn.execute(q, args).fetchall()

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in
                self.conn.execute("SELECT status, COUNT(*) n FROM videos GROUP BY status")}

    def bytes_done(self) -> int:
        r = self.conn.execute("SELECT COALESCE(SUM(filesize),0) n FROM videos WHERE status='done'").fetchone()
        return int(r["n"])

    def count_done(self) -> int:
        r = self.conn.execute("SELECT COUNT(*) n FROM videos WHERE status='done'").fetchone()
        return int(r["n"])

    def by_source(self):
        return self.conn.execute(
            "SELECT source, COUNT(*) n, COALESCE(SUM(filesize),0) bytes "
            "FROM videos WHERE status='done' GROUP BY source ORDER BY n DESC").fetchall()

    def total_duration(self) -> float:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(duration),0) d FROM videos WHERE status='done'").fetchone()
        return float(r["d"])

    def requeue(self, status: str = "failed") -> int:
        cur = self.conn.execute(
            "UPDATE videos SET status='candidate', attempts=0, error=NULL WHERE status=?", (status,))
        self.conn.commit()
        return cur.rowcount
