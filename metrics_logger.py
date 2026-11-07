"""Per-job metrics log — the data layer the monitoring agent queries.

Every completed job (success or failure) gets one row.  The monitoring agent
reads aggregates; retention is configurable.

Usage:
    from metrics_logger import log_job, summary_last_24h, recent_failures
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

_DB_PATH = os.getenv("METRICS_DB", os.path.join(os.path.dirname(__file__), "data", "metrics.db"))
_RETENTION_DAYS = int(os.getenv("METRICS_RETENTION_DAYS", "90"))

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS job_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    status          TEXT NOT NULL,
    doc_class       TEXT,
    src_lang        TEXT,
    tgt_lang        TEXT,
    jurisdiction    TEXT,
    tokens_used     INTEGER DEFAULT 0,
    duration_sec    REAL DEFAULT 0,
    segments_count  INTEGER DEFAULT 0,
    flags_raised    INTEGER DEFAULT 0,
    error_msg       TEXT,
    completed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_completed ON job_metrics(completed_at);
CREATE INDEX IF NOT EXISTS idx_metrics_status ON job_metrics(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    db = sqlite3.connect(_DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    with _conn() as db:
        db.executescript(_SCHEMA)


init_db()


# ── Write ────────────────────────────────────────────────────────────────────

def log_job(*, job_id: str, status: str, doc_class: str = "", src_lang: str = "",
            tgt_lang: str = "", jurisdiction: str = "", tokens_used: int = 0,
            duration_sec: float = 0, segments_count: int = 0, flags_raised: int = 0,
            error_msg: str = ""):
    """Record one job's outcome."""
    with _conn() as db:
        db.execute(
            "INSERT INTO job_metrics "
            "(job_id, status, doc_class, src_lang, tgt_lang, jurisdiction, "
            "tokens_used, duration_sec, segments_count, flags_raised, error_msg, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, status, doc_class, src_lang, tgt_lang, jurisdiction,
             tokens_used, duration_sec, segments_count, flags_raised, error_msg, _now()),
        )


# ── Read (for monitoring agent) ──────────────────────────────────────────────

def summary_last_24h() -> dict:
    """Aggregate metrics for the last 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _conn() as db:
        row = db.execute(
            "SELECT "
            "  COUNT(*) as total_jobs, "
            "  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success, "
            "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed, "
            "  SUM(tokens_used) as total_tokens, "
            "  AVG(duration_sec) as avg_duration, "
            "  AVG(tokens_used) as avg_tokens, "
            "  SUM(segments_count) as total_segments, "
            "  SUM(flags_raised) as total_flags "
            "FROM job_metrics WHERE completed_at >= ?",
            (cutoff,),
        ).fetchone()
    d = dict(row)
    total = d["total_jobs"] or 0
    success = d["success"] or 0
    d["success_rate"] = round(success / total * 100, 1) if total else 0.0
    return d


def summary_last_n_days(days: int = 7) -> dict:
    """Same as summary_last_24h but over N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as db:
        row = db.execute(
            "SELECT "
            "  COUNT(*) as total_jobs, "
            "  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success, "
            "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed, "
            "  SUM(tokens_used) as total_tokens, "
            "  AVG(duration_sec) as avg_duration, "
            "  SUM(segments_count) as total_segments "
            "FROM job_metrics WHERE completed_at >= ?",
            (cutoff,),
        ).fetchone()
    d = dict(row)
    total = d["total_jobs"] or 0
    success = d["success"] or 0
    d["success_rate"] = round(success / total * 100, 1) if total else 0.0
    return d


def recent_failures(limit: int = 20) -> list[dict]:
    """Most recent failed jobs — for root-cause analysis."""
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM job_metrics WHERE status = 'failed' "
            "ORDER BY completed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def daily_breakdown(days: int = 7) -> list[dict]:
    """Per-day aggregates for the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as db:
        rows = db.execute(
            "SELECT "
            "  DATE(completed_at) as day, "
            "  COUNT(*) as total, "
            "  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success, "
            "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed, "
            "  SUM(tokens_used) as tokens, "
            "  AVG(duration_sec) as avg_duration "
            "FROM job_metrics WHERE completed_at >= ? "
            "GROUP BY DATE(completed_at) ORDER BY day DESC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def purge_old(days: int = _RETENTION_DAYS) -> int:
    """Delete metrics older than N days.  Returns rows deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as db:
        cur = db.execute("DELETE FROM job_metrics WHERE completed_at < ?", (cutoff,))
    return cur.rowcount
