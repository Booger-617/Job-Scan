from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id             TEXT PRIMARY KEY,
    source         TEXT,
    company        TEXT,
    title          TEXT,
    location       TEXT,
    url            TEXT,
    posted_at      TEXT,
    first_seen     TEXT,
    last_seen      TEXT,
    rule_score     INTEGER,
    rule_notes     TEXT,
    blockers       TEXT,
    llm_score      INTEGER,
    llm_verdict    TEXT,
    llm_reason     TEXT,
    llm_match      TEXT,
    llm_gap        TEXT,
    sponsor_risk   TEXT,
    final_score    INTEGER,
    status         TEXT DEFAULT 'new',
    notes          TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_score ON jobs(final_score);
CREATE INDEX IF NOT EXISTS idx_status ON jobs(status);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def known_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT id FROM jobs")}

    def llm_scored_ids(self) -> set[str]:
        """Postings Claude has already judged - re-sending them is pure waste."""
        return {r[0] for r in
                self.db.execute("SELECT id FROM jobs WHERE llm_score IS NOT NULL")}

    def upsert(self, job: Job) -> bool:
        """Returns True when this is the first time we have seen the posting."""
        now = datetime.now(timezone.utc).isoformat()
        r = job.to_row()
        cur = self.db.execute("SELECT id FROM jobs WHERE id=?", (r["id"],))
        is_new = cur.fetchone() is None
        if is_new:
            self.db.execute(
                """INSERT INTO jobs (id,source,company,title,location,url,posted_at,
                       first_seen,last_seen,rule_score,rule_notes,blockers,llm_score,
                       llm_verdict,llm_reason,llm_match,llm_gap,sponsor_risk,final_score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["id"], r["source"], r["company"], r["title"], r["location"], r["url"],
                 r["posted_at"], now, now, r["rule_score"], r["rule_notes"], r["blockers"],
                 r["llm_score"], r["llm_verdict"], r["llm_reason"], r["llm_match"],
                 r["llm_gap"], r["sponsorship_risk"], r["final_score"]),
            )
        else:
            # Refresh rule scores, but never clobber a stored LLM verdict with the
            # blanks carried by a job that was not re-scored this run - and never
            # touch status/notes. In UPDATE, every right-hand side sees the OLD row,
            # so COALESCE falls back to what is already stored.
            self.db.execute(
                """UPDATE jobs SET last_seen=?, rule_score=?, rule_notes=?, blockers=?,
                       llm_score    = COALESCE(?, llm_score),
                       llm_verdict  = COALESCE(NULLIF(?,''), llm_verdict),
                       llm_reason   = COALESCE(NULLIF(?,''), llm_reason),
                       llm_match    = COALESCE(NULLIF(?,''), llm_match),
                       llm_gap      = COALESCE(NULLIF(?,''), llm_gap),
                       sponsor_risk = COALESCE(NULLIF(?,''), sponsor_risk),
                       final_score  = COALESCE(?, llm_score, ?)
                   WHERE id=?""",
                (now, r["rule_score"], r["rule_notes"], r["blockers"], r["llm_score"],
                 r["llm_verdict"], r["llm_reason"], r["llm_match"], r["llm_gap"],
                 r["sponsorship_risk"], r["llm_score"], r["rule_score"], r["id"]),
            )
        self.db.commit()
        return is_new

    def recent(self, days: int = 7, min_score: int = 0, limit: int = 500):
        return self.db.execute(
            """SELECT * FROM jobs
               WHERE julianday('now') - julianday(first_seen) <= ?
                 AND final_score >= ?
               ORDER BY final_score DESC, first_seen DESC LIMIT ?""",
            (days, min_score, limit),
        ).fetchall()

    def set_status(self, job_id_prefix: str, status: str, notes: str = "") -> int:
        cur = self.db.execute(
            "UPDATE jobs SET status=?, notes=COALESCE(NULLIF(?,''), notes) WHERE id LIKE ?",
            (status, notes, job_id_prefix + "%"),
        )
        self.db.commit()
        return cur.rowcount

    def stats(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) n, SUM(status='applied') applied, SUM(status='new') new FROM jobs"
        ).fetchone()
        return {"total": row["n"] or 0, "applied": row["applied"] or 0, "new": row["new"] or 0}

    def close(self):
        self.db.close()
