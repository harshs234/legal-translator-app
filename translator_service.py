"""FastAPI service that exposes the translation pipeline as HTTP endpoints.

n8n (or any scheduler) calls these endpoints to trigger and monitor translations.
Run:  uvicorn translator_service:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    POST /enqueue          — add a job (file path + lang pair)
    POST /translate/{id}   — run one specific job now
    GET  /jobs             — list jobs (optional ?status=queued)
    GET  /jobs/{id}        — single job detail
    GET  /health           — service health check
    GET  /metrics/summary  — last-24h aggregate (for monitoring agent)
"""
import os
import sys
import time
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(__file__))

import queue_manager as qm
import metrics_logger as ml
import inbox_watcher
from classifier import classify
from config import ALLOWED_PAIRS
from docreader import extract_text, detect_language
from export import to_docx, clean_and_validate
from translator import translate_document

app = FastAPI(title="Mutarjim Scheduled Translation Service", version="1.0.0")

# Create inbox/processed/output folders on startup so the user never has to.
inbox_watcher.ensure_dirs()


# ── Request / response models ────────────────────────────────────────────────

class EnqueueRequest(BaseModel):
    input_file: str
    src_lang: str = ""       # auto-detect if blank
    tgt_lang: str = ""       # infer from src_lang if blank
    priority: int = 0
    max_retries: int = 3


class EnqueueResponse(BaseModel):
    job_id: str
    status: str = "queued"


class TranslateResponse(BaseModel):
    job_id: str
    status: str
    output_file: str | None = None
    doc_class: str | None = None
    segments_count: int = 0
    flags_raised: int = 0
    duration_sec: float = 0
    error: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "queue": qm.count_by_status()}


@app.post("/enqueue", response_model=EnqueueResponse)
def enqueue_job(req: EnqueueRequest):
    """Add a document to the translation queue."""
    if not os.path.isfile(req.input_file):
        raise HTTPException(400, f"File not found: {req.input_file}")

    # Auto-detect languages if not provided
    src = req.src_lang
    tgt = req.tgt_lang
    if not src or not tgt:
        with open(req.input_file, "rb") as f:
            raw = f.read()
        text = extract_text(os.path.basename(req.input_file), raw)
        if not src:
            src = detect_language(text)
        if not tgt:
            tgt = "en" if src in ("ar", "fr") else "ar"

    if (src, tgt) not in ALLOWED_PAIRS:
        raise HTTPException(400, f"Unsupported direction {src}->{tgt}")

    job_id = qm.enqueue(req.input_file, src, tgt, req.priority, req.max_retries)
    return EnqueueResponse(job_id=job_id)


@app.post("/translate/{job_id}", response_model=TranslateResponse)
def translate_job(job_id: str):
    """Execute a specific queued job now.  Called by n8n's HTTP node."""
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] not in ("queued", "needs_review"):
        raise HTTPException(409, f"Job status is '{job['status']}', expected 'queued'")

    return _run_job(job)


@app.post("/translate-next", response_model=TranslateResponse)
def translate_next():
    """Atomically claim the next queued job and run it.  Convenience for n8n:
    one call does claim + translate.  Returns 204 when the queue is empty.
    Safe to call from overlapping schedule ticks — claim_next() guarantees no
    two callers get the same job."""
    # recover any stale running jobs first (crash recovery)
    qm.recover_stale_jobs()

    job = qm.claim_next()          # atomic: already marked 'running'
    if not job:
        raise HTTPException(204, "Queue empty")

    return _run_job(job, already_claimed=True)


@app.post("/scan-inbox")
def scan_inbox():
    """Scan the inbox/ drop-folders and enqueue any new documents.

    n8n calls this at the start of each scheduled cycle so that dropping a file
    into inbox/to_english (etc.) is all a user ever has to do."""
    return inbox_watcher.scan_inbox()


@app.get("/jobs")
def list_jobs(status: str | None = None, limit: int = 50):
    return qm.list_jobs(status, limit)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/metrics/summary")
def metrics_summary():
    return ml.summary_last_24h()


@app.get("/metrics/daily")
def metrics_daily(days: int = 7):
    return ml.daily_breakdown(days)


@app.get("/metrics/failures")
def metrics_failures(limit: int = 20):
    return ml.recent_failures(limit)


# ── Core translation runner ──────────────────────────────────────────────────

def _run_job(job: dict, already_claimed: bool = False) -> TranslateResponse:
    """Execute the full pipeline: extract → classify → translate → export → log.
    If already_claimed is True the job is already marked 'running' (via
    claim_next) and we don't re-mark it."""
    job_id = job["job_id"]
    if not already_claimed:
        qm.mark_running(job_id)
    t0 = time.time()

    try:
        # 1. Read the document
        with open(job["input_file"], "rb") as f:
            raw = f.read()
        text = extract_text(os.path.basename(job["input_file"]), raw)
        src = job["src_lang"]
        tgt = job["tgt_lang"]

        # 2. Classify
        cls = classify(text)
        doc_class = cls["doc_class"]
        jurisdiction = cls.get("jurisdiction", "")
        confidence = cls.get("confidence", 0.0)

        # If confidence is too low, park for human review — UNLESS auto-approve
        # is enabled (AUTO_APPROVE_LOW_CONFIDENCE=1), in which case we proceed
        # with the best-guess class and never stop for a human.
        from config import settings
        auto_approve = os.getenv("AUTO_APPROVE_LOW_CONFIDENCE", "").lower() in ("1", "true", "yes")
        if confidence < settings.min_class_confidence and not auto_approve:
            qm.mark_needs_review(
                job_id,
                f"Low classification confidence ({confidence:.0%}): {cls.get('reason', '')}. "
                f"Detected as '{doc_class}'. Needs human confirmation."
            )
            duration = time.time() - t0
            ml.log_job(
                job_id=job_id, status="needs_review", doc_class=doc_class,
                src_lang=src, tgt_lang=tgt, jurisdiction=jurisdiction,
                duration_sec=duration, error_msg="Low confidence — needs review",
            )
            return TranslateResponse(
                job_id=job_id, status="needs_review", doc_class=doc_class,
                duration_sec=round(duration, 2),
                error=f"Classification confidence {confidence:.0%} below threshold",
            )

        # 3. Translate
        records = translate_document(text, src, tgt, doc_class, jurisdiction)
        segments_count = len(records)
        flags_raised = sum(len(r.get("flags", [])) for r in records)

        # 4. Export to DOCX
        paragraphs = [r["target"] for r in records]
        target_is_arabic = tgt == "ar"
        docx_bytes = to_docx(paragraphs, rtl=target_is_arabic)

        # Write output into the shared output/ folder
        output_file = inbox_watcher.output_path_for(job["input_file"], src, tgt)
        with open(output_file, "wb") as f:
            f.write(docx_bytes)

        duration = time.time() - t0

        # 5. Mark done + log metrics
        qm.mark_done(job_id, output_file, doc_class, jurisdiction,
                      segments_count, flags_raised)
        ml.log_job(
            job_id=job_id, status="success", doc_class=doc_class,
            src_lang=src, tgt_lang=tgt, jurisdiction=jurisdiction,
            duration_sec=duration, segments_count=segments_count,
            flags_raised=flags_raised,
        )

        # 6. Move the source out of the inbox so it isn't picked up again
        inbox_watcher.archive_source(job["input_file"])

        return TranslateResponse(
            job_id=job_id, status="done", output_file=output_file,
            doc_class=doc_class, segments_count=segments_count,
            flags_raised=flags_raised, duration_sec=round(duration, 2),
        )

    except Exception as exc:
        duration = time.time() - t0
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        permanently_failed = qm.mark_failed(job_id, str(exc))
        ml.log_job(
            job_id=job_id, status="failed",
            src_lang=job["src_lang"], tgt_lang=job["tgt_lang"],
            duration_sec=duration, error_msg=str(exc),
        )
        # Only once retries are exhausted do we move the source to failed/;
        # while it still has retries left it stays in the inbox for another go.
        if permanently_failed:
            inbox_watcher.move_failed(job["input_file"])
        return TranslateResponse(
            job_id=job_id, status="failed",
            duration_sec=round(duration, 2), error=str(exc),
        )
