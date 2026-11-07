"""Build/refresh the RAG stores from corpus/curated/*.csv and corpus/processed/*.jsonl.

Every record is indexed in both directions (swapped), restricted to the allowed pairs
(ar<->en, fr<->en). Terms go to SQLite (exact lookup); sentences/paragraphs go to ChromaDB.

Usage:
  python build_index.py                      # build everything (can be slow: embeds all passages)
  python build_index.py --reset              # wipe both stores first, then build
  python build_index.py --max-passages 25000 # FAST first build: cap passages per source file
                                             #   (terminology is always loaded in full)

Tip: do a capped build first (e.g. --max-passages 25000 --reset) to validate the whole
pipeline with the API, then re-run without the cap to index everything.
"""
import argparse
import csv
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from config import settings, ALLOWED_PAIRS, VALID_RECORD_CLASSES
from adapters.common import Record, clean
import termstore
import rag

FIELDS = [f for f in Record.__dataclass_fields__]


def load_records(max_passages=None):
    """Yield Records from curated CSVs and processed JSONL.
    If max_passages is set, cap the number of 'sentence' records taken per source file
    (terminology 'term' records are never capped)."""
    root = Path(settings.corpus_dir)
    for f in sorted((root / "curated").glob("*.csv")):
        with open(f, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                yield Record(**({k: (row.get(k) or "") for k in FIELDS} | {"status": row.get("status") or "seed"}))
    for f in sorted((root / "processed").glob("*.jsonl")):
        sent_count = 0
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = Record(**json.loads(line))
                if rec.kind == "sentence":
                    if max_passages is not None and sent_count >= max_passages:
                        continue
                    sent_count += 1
                yield rec


def both_directions(r: Record):
    yield r
    yield Record(**{**r.__dict__, "src_lang": r.tgt_lang, "tgt_lang": r.src_lang, "source": r.target, "target": r.source})


def main(reset=False, max_passages=None):
    col = rag.collection()
    if reset:
        ids = col.get()["ids"]
        for i in range(0, len(ids), 500):
            col.delete(ids=ids[i:i + 500])
        termstore.reset()

    stats, terms, sents, seen = Counter(), [], [], set()
    for rec in load_records(max_passages):
        rec.source, rec.target = clean(rec.source), clean(rec.target)
        if rec.doc_class not in VALID_RECORD_CLASSES or rec.kind not in ("term", "sentence") \
                or (rec.src_lang, rec.tgt_lang) not in ALLOWED_PAIRS or not rec.source or not rec.target:
            stats["rejected"] += 1
            continue
        # Drop useless terminology where the source and target are identical
        # (proper nouns / acronyms like "BCLI -> BCLI"): as a mandatory glossary
        # entry it only adds noise. Sentences are never filtered this way.
        if rec.kind == "term" and rec.source.lower() == rec.target.lower():
            stats["identical_term"] += 1
            continue
        for r in both_directions(rec):
            rid = hashlib.sha1("|".join([r.doc_class, r.src_lang, r.tgt_lang, r.kind, r.jurisdiction, r.source, r.target]).encode()).hexdigest()
            if rid in seen:
                stats["duplicate"] += 1
                continue
            seen.add(rid)
            (terms if r.kind == "term" else sents).append((rid, r))

    added = termstore.add_terms(r for _, r in terms)

    # Embed + index passages in batches, with progress (this is the slow part).
    total = len(sents)
    t0 = time.time()
    print(f"Embedding {total:,} passages (this is the slow step)...")
    for i in range(0, total, 128):
        part = sents[i:i + 128]
        col.upsert(
            ids=[rid for rid, _ in part],
            documents=[r.source for _, r in part],
            embeddings=rag.embed([r.source for _, r in part]),
            metadatas=[{"doc_class": r.doc_class, "src_lang": r.src_lang, "tgt_lang": r.tgt_lang,
                        "target": r.target, "jurisdiction": r.jurisdiction, "section": r.section,
                        "status": r.status, "origin": r.origin} for _, r in part],
        )
        done = min(i + 128, total)
        if done % 2560 == 0 or done == total:
            rate = done / max(time.time() - t0, 1e-6)
            eta = (total - done) / max(rate, 1e-6)
            print(f"  {done:,}/{total:,}  ({rate:.0f}/s, ETA {eta/60:.1f} min)")

    print(f"\nterms: +{added} (store total {termstore.count()}) | passages indexed: {total} "
          f"(collection total {col.count()}) | rejected: {stats['rejected']} | "
          f"identical-terms dropped: {stats['identical_term']} | duplicates: {stats['duplicate']}")
    return {"terms_added": added, "passages": total, **stats}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe both stores first")
    ap.add_argument("--max-passages", type=int, default=None,
                    help="cap sentence passages per source file (fast first build)")
    args = ap.parse_args()
    main(args.reset, args.max_passages)
