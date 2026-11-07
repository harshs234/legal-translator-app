"""Job queue backed by SQLite — the shared state both the scheduled agent and
the monitoring agent read/write.

Usage:
    from queue_manager import enqueue, next_pending, mark_running, mark_done, mark_failed

Tables are auto-created on first import.
"""
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

_DB_PATH = os.getenv("QUEUE_DB", os.path.join(os.path.dirname(__file__), "data", "queue.db"))
_MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
_STALE_THRESHOLD_SEC = int(os.getenv("STALE_JOB_THRESHOLD_SEC", "1800"))  # 30 min

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    input_file      TEXT NOT NULL,
    output_file     TEXT,
    src_lang        TEXT NOT NULL,
    tgt_lang        TEXT NOT NULL,
    doc_class       TEXT,
    jurisdiction    TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    priority        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    error_msg       TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    segments_count  INTEGER,
    flags_raised    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority DESC, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    db = sqlite3.connect(_DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")  # safe for concurrent reads
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    with _conn() as db:
        db.executescript(_SCHEMA)


# auto-init on import
init_db()


# ── Enqueue ──────────────────────────────────────────────────────────────────

def enqueue(input_file: str, src_lang: str, tgt_lang: str,
            priority: int = 0, max_retries: int = _MAX_RETRIES) -> str:
    """Add a translation job to the queue.  Returns the job_id (UUID)."""
    job_id = uuid.uuid4().hex
    with _conn() as db:
        db.execute(
            "INSERT INTO jobs (job_id, input_file, src_lang, tgt_lang, priority, "
            "max_retries, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, input_file, src_lang, tgt_lang, priority, max_retries, _now()),
        )
    return job_id


def enqueue_batch(jobs: list[dict]) -> list[str]:
    """Enqueue multiple jobs atomically.  Each dict needs input_file, src_lang,
    tgt_lang; priority and max_retries are optional."""
    ids = []
    with _conn() as db:
        for j in jobs:
            jid = uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs (job_id, input_file, src_lang, tgt_lang, priority, "
                "max_retries, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (jid, j["input_file"], j["src_lang"], j["tgt_lang"],
                 j.get("priority", 0), j.get("max_retries", _MAX_RETRIES), _now()),
            )
            ids.append(jid)
    return ids


# ── Fetch ────────────────────────────────────────────────────────────────────

def next_pending() -> dict | None:
    """Return the highest-priority oldest queued job (as a dict), or None.
    Read-only peek — does NOT claim the job.  Use claim_next() to actually take
    a job for processing."""
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE status = 'queued' "
            "ORDER BY priority DESC, created_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def claim_next() -> dict | None:
    """Atomically take the highest-priority oldest queued job: flip it to
    'running' and return it, in a single locked transaction.  Returns None if
    the queue is empty.

    This is the safe entry point for the scheduler.  Because the SELECT and the
    UPDATE happen inside one BEGIN IMMEDIATE transaction, two overlapping
    schedule ticks can never grab the same job — the second caller blocks, then
    sees the row is already 'running' and moves on to the next one."""
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    db = sqlite3.connect(_DB_PATH, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM jobs WHERE status = 'queued' "
            "ORDER BY priority DESC, created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            db.execute("COMMIT")
            return None
        db.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            (_now(), row["job_id"]),
        )
        db.execute("COMMIT")
        return dict(row)
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def get_job(job_id: str) -> dict | None:
    with _conn() as db:
        row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def job_exists_for_file(input_file: str) -> bool:
    """True if any job (any status) already references this input file.
    Used by the inbox watcher to avoid enqueuing the same document twice."""
    with _conn() as db:
        row = db.execute(
            "SELECT 1 FROM jobs WHERE input_file = ? LIMIT 1", (input_file,)
        ).fetchone()
    return row is not None


def list_jobs(status: str | None = None, limit: int = 50) -> list[dict]:
    with _conn() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── Status transitions ───────────────────────────────────────────────────────

def mark_running(job_id: str):
    with _conn() as db:
        db.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
            (_now(), job_id),
        )


def mark_done(job_id: str, output_file: str, doc_class: str = "",
              jurisdiction: str = "", segments_count: int = 0, flags_raised: int = 0):
    with _conn() as db:
        db.execute(
            "UPDATE jobs SET status = 'done', output_file = ?, completed_at = ?, "
            "doc_class = ?, jurisdiction = ?, segments_count = ?, flags_raised = ? "
            "WHERE job_id = ?",
            (output_file, _now(), doc_class, jurisdiction, segments_count,
             flags_raised, job_id),
        )


def mark_failed(job_id: str, error_msg: str) -> bool:
    """Increment retry count.  If retries left, set back to 'queued'; else 'failed'.
    Returns True if the job has now PERMANENTLY failed (retries exhausted),
    False if it was put back in the queue for another attempt."""
    with _conn() as db:
        row = db.execute(
            "SELECT retry_count, max_retries FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row:
            return False
        new_count = row["retry_count"] + 1
        if new_count < row["max_retries"]:
            db.execute(
                "UPDATE jobs SET status = 'queued', retry_count = ?, error_msg = ? "
                "WHERE job_id = ?",
                (new_count, error_msg, job_id),
            )
            return False
        else:
            db.execute(
                "UPDATE jobs SET status = 'failed', retry_count = ?, error_msg = ?, "
                "completed_at = ? WHERE job_id = ?",
                (new_count, error_msg, _now(), job_id),
            )
            return True


def mark_needs_review(job_id: str, reason: str):
    """Low-confidence classification — park until a human confirms."""
    with _conn() as db:
        db.execute(
            "UPDATE jobs SET status = 'needs_review', error_msg = ? WHERE job_id = ?",
            (reason, job_id),
        )


# ── Recovery ─────────────────────────────────────────────────────────────────

def recover_stale_jobs() -> int:
    """Reset jobs stuck in 'running' (agent crashed mid-job) back to 'queued'.
    Returns the number of recovered jobs."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=_STALE_THRESHOLD_SEC)).isoformat()
    with _conn() as db:
        cur = db.execute(
            "UPDATE jobs SET status = 'queued' "
            "WHERE status = 'running' AND started_at < ?",
            (cutoff,),
        )
    return cur.rowcount


# ── Stats (for monitoring agent) ─────────────────────────────────────────────

def count_by_status() -> dict[str, int]:
    with _conn() as db:
        rows = db.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}
