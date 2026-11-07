"""Standalone scheduled translation agent.

Can run in three modes:
    1. --once         Process one job and exit (for cron / n8n "Execute Command" node)
    2. --batch N      Process up to N jobs and exit
    3. --daemon       Run forever, polling every --interval seconds

All modes share the same core loop: fetch → translate → log → repeat.
This script does NOT need FastAPI running — it imports the pipeline directly.

Usage:
    python scheduled_agent.py --once
    python scheduled_agent.py --batch 10
    python scheduled_agent.py --daemon --interval 3600
"""
import argparse
import logging
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(__file__))

import queue_manager as qm
import metrics_logger as ml
import inbox_watcher
from classifier import classify
from config import settings, ALLOWED_PAIRS
from docreader import extract_text
from export import to_docx
from translator import translate_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "data", "agent.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("scheduled_agent")


def scan_inbox() -> int:
    """Pull any newly-dropped documents from inbox/ into the queue.
    Returns how many files were enqueued."""
    result = inbox_watcher.scan_inbox()
    if result["enqueued_count"]:
        log.info(f"Inbox scan: enqueued {result['enqueued_count']} new file(s)")
        for e in result["enqueued"]:
            log.info(f"  + {os.path.basename(e['file'])} ({e['direction']})")
    for s in result["skipped"]:
        log.warning(f"  Skipped {os.path.basename(s['file'])}: {s['reason']}")
    return result["enqueued_count"]


def process_one_job() -> bool:
    """Fetch the next queued job, run the full pipeline, log metrics.
    Returns True if a job was processed, False if queue was empty."""

    # Recover any jobs stuck from a previous crash
    recovered = qm.recover_stale_jobs()
    if recovered:
        log.info(f"Recovered {recovered} stale job(s) back to queue")

    job = qm.claim_next()          # atomic: already marked 'running'
    if not job:
        log.info("Queue empty — nothing to do")
        return False

    job_id = job["job_id"]
    log.info(f"Processing job {job_id}: {job['input_file']} ({job['src_lang']}→{job['tgt_lang']})")
    t0 = time.time()

    try:
        # 1. Read document
        if not os.path.isfile(job["input_file"]):
            raise FileNotFoundError(f"Input file missing: {job['input_file']}")
        with open(job["input_file"], "rb") as f:
            raw = f.read()
        text = extract_text(os.path.basename(job["input_file"]), raw)
        log.info(f"  Extracted {len(text)} chars from {os.path.basename(job['input_file'])}")

        src, tgt = job["src_lang"], job["tgt_lang"]
        if (src, tgt) not in ALLOWED_PAIRS:
            raise ValueError(f"Unsupported direction {src}->{tgt}")

        # 2. Classify
        cls = classify(text)
        doc_class = cls["doc_class"]
        jurisdiction = cls.get("jurisdiction", "")
        confidence = cls.get("confidence", 0.0)
        log.info(f"  Classified as '{doc_class}' ({confidence:.0%}), jurisdiction={jurisdiction}")

        auto_approve = os.getenv("AUTO_APPROVE_LOW_CONFIDENCE", "").lower() in ("1", "true", "yes")
        if confidence < settings.min_class_confidence and not auto_approve:
            qm.mark_needs_review(
                job_id,
                f"Low confidence ({confidence:.0%}): {cls.get('reason', '')}. "
                f"Detected as '{doc_class}'."
            )
            ml.log_job(
                job_id=job_id, status="needs_review", doc_class=doc_class,
                src_lang=src, tgt_lang=tgt, jurisdiction=jurisdiction,
                duration_sec=time.time() - t0,
                error_msg="Low classification confidence",
            )
            log.warning(f"  Job {job_id} parked for review (confidence {confidence:.0%})")
            return True

        # 3. Translate
        log.info(f"  Translating {src}→{tgt} (class={doc_class})...")
        records = translate_document(text, src, tgt, doc_class, jurisdiction)
        segments_count = len(records)
        flags_raised = sum(len(r.get("flags", [])) for r in records)
        log.info(f"  Translated: {segments_count} segments, {flags_raised} flags")

        # 4. Export
        paragraphs = [r["target"] for r in records]
        docx_bytes = to_docx(paragraphs, rtl=(tgt == "ar"))

        output_file = inbox_watcher.output_path_for(job["input_file"], src, tgt)
        with open(output_file, "wb") as f:
            f.write(docx_bytes)
        log.info(f"  Exported to {output_file}")

        duration = time.time() - t0

        # 5. Mark done + log
        qm.mark_done(job_id, output_file, doc_class, jurisdiction,
                      segments_count, flags_raised)
        ml.log_job(
            job_id=job_id, status="success", doc_class=doc_class,
            src_lang=src, tgt_lang=tgt, jurisdiction=jurisdiction,
            tokens_used=0,  # TODO: wire LiteLLM token counting when available
            duration_sec=duration, segments_count=segments_count,
            flags_raised=flags_raised,
        )
        # Move the source out of the inbox so it isn't picked up again
        inbox_watcher.archive_source(job["input_file"])
        log.info(f"  ✓ Job {job_id} done in {duration:.1f}s")
        return True

    except Exception as exc:
        duration = time.time() - t0
        err_msg = str(exc)
        permanently_failed = qm.mark_failed(job_id, err_msg)
        ml.log_job(
            job_id=job_id, status="failed",
            src_lang=job["src_lang"], tgt_lang=job["tgt_lang"],
            duration_sec=duration, error_msg=err_msg,
        )
        if permanently_failed:
            moved = inbox_watcher.move_failed(job["input_file"])
            where = f" (moved to failed/)" if moved else ""
            log.error(f"  ✗ Job {job_id} PERMANENTLY failed after {duration:.1f}s "
                      f"(retries exhausted): {err_msg}{where}")
        else:
            log.warning(f"  ✗ Job {job_id} failed after {duration:.1f}s, "
                        f"requeued for retry: {err_msg}")
        log.debug(traceback.format_exc())
        return True  # a job existed, even though it failed


def run_batch(max_jobs: int):
    """Scan the inbox, then process up to max_jobs, then exit."""
    scan_inbox()
    processed = 0
    for _ in range(max_jobs):
        if process_one_job():
            processed += 1
        else:
            break
    log.info(f"Batch complete: {processed} job(s) processed")
    return processed


def run_daemon(interval: int, max_per_cycle: int = 10):
    """Run forever, polling every `interval` seconds."""
    log.info(f"Daemon started — polling every {interval}s, max {max_per_cycle} jobs/cycle")
    try:
        while True:
            processed = run_batch(max_per_cycle)
            if processed == 0:
                log.info(f"Queue empty, sleeping {interval}s...")
            else:
                log.info(f"Cycle done ({processed} jobs), sleeping {interval}s...")
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Daemon stopped by user")


def main():
    # Ensure data directory exists for logs and DBs
    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)
    # Ensure inbox/processed/output folders exist (so the user never creates them)
    inbox_watcher.ensure_dirs()

    parser = argparse.ArgumentParser(description="Mutarjim Scheduled Translation Agent")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Process one job and exit")
    mode.add_argument("--batch", type=int, metavar="N", help="Process up to N jobs and exit")
    mode.add_argument("--daemon", action="store_true", help="Run forever, polling")
    parser.add_argument("--interval", type=int, default=3600,
                        help="Seconds between polls in daemon mode (default: 3600)")
    parser.add_argument("--max-per-cycle", type=int, default=10,
                        help="Max jobs per cycle in daemon mode (default: 10)")
    args = parser.parse_args()

    if args.once:
        scan_inbox()
        process_one_job()
    elif args.batch:
        run_batch(args.batch)
    elif args.daemon:
        run_daemon(args.interval, args.max_per_cycle)


if __name__ == "__main__":
    main()
