"""Convert downloaded official data into corpus/processed/<name>.jsonl, ready for build_index.py.

  tmx      your certified translation memories (best source)         ingest.py tmx FILE.tmx --src ar --tgt en --doc-class court_judgment --jurisdiction AE --status approved
  numbered ECHR / SCC style judgments, one text file per language    ingest.py numbered --src-file en.txt --tgt-file fr.txt --src en --tgt fr --jurisdiction CA
  justice  Canada Justice Laws bulk XML (eng dir + fra dir)          ingest.py justice --eng-dir DIR --fra-dir DIR --doc-class legislation
  lines    UN Parallel Corpus line-aligned files                     ingest.py lines --src-file X.ar --tgt-file X.en --src ar --tgt en --legal-only --limit 200000
  tbx      IATE TBX export                                           ingest.py tbx FILE.tbx --src en --tgt fr --doc-class common
  termium  TERMIUM Plus CSV/zip                                      ingest.py termium FILE.zip [--inspect]
Add --dry-run to print counts and 8 sample records without writing.
"""
import argparse
import sys
from itertools import islice
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings, ALLOWED_PAIRS, VALID_RECORD_CLASSES  # noqa: E402
from adapters.common import Record, write_jsonl, good_pair  # noqa: E402
from adapters import formats, court, legislation  # noqa: E402


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["tmx", "numbered", "justice", "lines", "tbx", "termium"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--src"); ap.add_argument("--tgt")
    ap.add_argument("--src-file"); ap.add_argument("--tgt-file")
    ap.add_argument("--eng-dir"); ap.add_argument("--fra-dir")
    ap.add_argument("--doc-class", default=None)
    ap.add_argument("--jurisdiction", default="")
    ap.add_argument("--origin", default="")
    ap.add_argument("--status", default="official", choices=["official", "approved", "seed"])
    ap.add_argument("--name", default=None, help="output file stem")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--legal-only", action="store_true")
    ap.add_argument("--en-col"); ap.add_argument("--fr-col")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    return ap


def records(a):
    common = dict(jurisdiction=a.jurisdiction, origin=a.origin or a.cmd, status=a.status)
    if a.cmd == "tmx":
        for s, t in formats.read_tmx(a.file, a.src, a.tgt):
            if good_pair(s, t):
                yield Record(a.doc_class or "common", a.src, a.tgt, "sentence", s, t, **common)
    elif a.cmd == "lines":
        for s, t in formats.read_lines(a.src_file, a.tgt_file, a.legal_only):
            if good_pair(s, t):
                yield Record(a.doc_class or "common", a.src, a.tgt, "sentence", s, t, **common)
    elif a.cmd == "numbered":
        s_txt = open(a.src_file, encoding="utf-8").read()
        t_txt = open(a.tgt_file, encoding="utf-8").read()
        for s, t, sec in court.align_numbered(s_txt, t_txt):
            yield Record(a.doc_class or "court_judgment", a.src, a.tgt, "sentence", s, t, section=sec, **common)
    elif a.cmd == "justice":
        for s, t in legislation.align_dirs(a.eng_dir, a.fra_dir):
            yield Record(a.doc_class or "legislation", "en", "fr", "sentence", s, t, **{**common, "jurisdiction": a.jurisdiction or "CA"})
    elif a.cmd == "tbx":
        for s, t in formats.read_tbx(a.file, a.src, a.tgt):
            yield Record(a.doc_class or "common", a.src, a.tgt, "term", s, t, **common)
    elif a.cmd == "termium":
        for en, fr in formats.read_termium(a.file, a.en_col, a.fr_col):
            yield Record(a.doc_class or "common", "en", "fr", "term", en, fr, **{**common, "jurisdiction": a.jurisdiction or "CA"})


def main():
    a = build_parser().parse_args()
    if a.cmd == "termium" and a.inspect:
        print(formats.termium_headers(a.file))
        return
    if a.cmd in ("tmx", "tbx", "lines", "numbered") and (a.src, a.tgt) not in ALLOWED_PAIRS:
        sys.exit(f"--src/--tgt must be one of {sorted(ALLOWED_PAIRS)} (no Arabic<->French)")
    if a.doc_class and a.doc_class not in VALID_RECORD_CLASSES:
        sys.exit(f"--doc-class must be one of {sorted(VALID_RECORD_CLASSES)}")
    recs = records(a)
    if a.limit:
        recs = islice(recs, a.limit)
    if a.dry_run:
        recs = list(recs)
        print(f"{len(recs)} records")
        for r in recs[:8]:
            print(f"- [{r.doc_class}/{r.section or '-'}] {r.source[:110]}\n    => {r.target[:110]}")
        return
    stem = a.name or f"{a.cmd}_{Path(a.file or a.src_file or a.eng_dir).stem}"
    out = Path(settings.corpus_dir) / "processed" / f"{stem}.jsonl"
    print(f"wrote {write_jsonl(out, recs)} records -> {out}")


if __name__ == "__main__":
    main()
