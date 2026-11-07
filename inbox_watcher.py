"""Inbox watcher — turns a drop-folder into queued translation jobs.

This is what makes the agent truly hands-off: instead of curl-enqueuing every
file by hand, you drop documents into a folder and the scheduler drains them.

Folder layout (all auto-created under the project root, or $INBOX_BASE):

    inbox/
        to_english/   drop Arabic or French documents here  -> translated to English
        to_arabic/    drop English documents here           -> translated to Arabic
        to_french/    drop English documents here           -> translated to French
    processed/        source files are moved here after a successful translation
    output/           the translated .docx files land here

The subfolder a file is dropped in decides the TARGET language.  The SOURCE
language is auto-detected from the document text.  This resolves the only
ambiguous case:

    Arabic  -> always English   (only ar->en is allowed)
    French  -> always English   (only fr->en is allowed)
    English -> Arabic OR French (you choose by which folder you drop it in)

Why the folder and not auto-detection for English?  Detection can only tell you
what language a document *is*, never which language you *want* it in.  English
has two valid targets, so the choice has to come from somewhere the machine can
read — the folder name.
"""
import os
import shutil
from datetime import datetime, timezone

import queue_manager as qm
from config import ALLOWED_PAIRS
from docreader import extract_text, detect_language

_BASE = os.getenv("INBOX_BASE", os.path.dirname(os.path.abspath(__file__)))
INBOX_DIR = os.path.join(_BASE, "inbox")
PROCESSED_DIR = os.path.join(_BASE, "processed")
FAILED_DIR = os.path.join(_BASE, "failed")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(_BASE, "output"))

# subfolder name -> target language code
_FOLDER_TARGET = {
    "to_english": "en",
    "to_arabic": "ar",
    "to_french": "fr",
}

_SUPPORTED_EXT = {".pdf", ".docx", ".txt"}


def ensure_dirs():
    """Create the inbox/processed/failed/output folders if they don't exist.
    Safe to call repeatedly; this is why you don't have to make folders by hand."""
    for sub in _FOLDER_TARGET:
        os.makedirs(os.path.join(INBOX_DIR, sub), exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    os.makedirs(FAILED_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def output_path_for(input_file: str, src: str, tgt: str) -> str:
    """Where the translated .docx for this job should be written."""
    ensure_dirs()
    base = os.path.splitext(os.path.basename(input_file))[0]
    return os.path.join(OUTPUT_DIR, f"{base}_{src}_to_{tgt}.docx")


def scan_inbox() -> dict:
    """Scan every inbox subfolder and enqueue new documents.

    Returns {enqueued: [...], skipped: [...], enqueued_count, skipped_count}.
    A file is skipped if a job already exists for it, if it can't be read, or if
    the detected direction isn't an allowed pair (e.g. an English file dropped in
    to_english, or a scanned PDF whose text layer is corrupt)."""
    ensure_dirs()
    enqueued, skipped = [], []

    for sub, tgt in _FOLDER_TARGET.items():
        folder = os.path.join(INBOX_DIR, sub)
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            if os.path.splitext(name)[1].lower() not in _SUPPORTED_EXT:
                continue

            # already queued / running / parked / failed? don't double-enqueue
            if qm.job_exists_for_file(path):
                continue

            # detect the source language from the document text
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                text = extract_text(name, raw)
                src = detect_language(text)
            except Exception as exc:
                skipped.append({"file": path, "reason": f"unreadable: {exc}"})
                continue

            if (src, tgt) not in ALLOWED_PAIRS:
                skipped.append({
                    "file": path,
                    "reason": f"direction {src}->{tgt} not allowed "
                              f"(detected source '{src}', folder targets '{tgt}')",
                })
                continue

            job_id = qm.enqueue(path, src, tgt)
            enqueued.append({"file": path, "job_id": job_id,
                             "direction": f"{src}->{tgt}"})

    return {
        "enqueued": enqueued,
        "skipped": skipped,
        "enqueued_count": len(enqueued),
        "skipped_count": len(skipped),
    }


def _relocate(input_file: str, dest_dir: str) -> str | None:
    """Move a source file out of the inbox into dest_dir, never overwriting.

    Only files that actually live inside the inbox are moved — a document that
    was enqueued by hand from some other folder is left exactly where it is.
    Nothing is ever deleted; a name clash gets a timestamp suffix.
    Returns the new path, or None if nothing was moved."""
    if not os.path.isfile(input_file):
        return None
    try:
        inside = os.path.commonpath(
            [os.path.abspath(input_file), os.path.abspath(INBOX_DIR)]
        ) == os.path.abspath(INBOX_DIR)
    except ValueError:
        # different drives on Windows -> not inside the inbox
        return None
    if not inside:
        return None

    ensure_dirs()
    dest = os.path.join(dest_dir, os.path.basename(input_file))
    if os.path.exists(dest):  # avoid clobbering a same-named earlier file
        stem, ext = os.path.splitext(os.path.basename(input_file))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = os.path.join(dest_dir, f"{stem}_{stamp}{ext}")
    try:
        shutil.move(input_file, dest)
        return dest
    except Exception:
        return None


def archive_source(input_file: str) -> str | None:
    """Move a successfully-translated source file into processed/."""
    return _relocate(input_file, PROCESSED_DIR)


def move_failed(input_file: str) -> str | None:
    """Move a permanently-failed source file (retries exhausted) into failed/,
    so it's collected in one place for a human to look at instead of silently
    clogging the inbox."""
    return _relocate(input_file, FAILED_DIR)
