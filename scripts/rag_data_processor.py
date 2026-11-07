#!/usr/bin/env python3
"""
RAG Data Processor for Legal Translator  (v3 - robust)
======================================================
Converts the downloaded source corpora into clean JSONL records in the exact
schema the RAG index builder expects.

It AUTO-FINDS the data folder (the one containing ar-en/, en-fr/, legis.xml),
whether you run it from inside RAG_DATA or from its parent.

Run:
    python rag_data_processor.py

Outputs (written into an 'rag_output' folder next to the data):
    processed/termium.jsonl        full EN->FR terminology (all of it)
    processed/legislation.jsonl    EN->FR Act-title pairs from legis.xml
    processed/un_ar_en.jsonl       AR->EN aligned sentences (capped)
    processed/un_en_fr.jsonl       EN->FR aligned sentences (capped)
    STRUCTURE.txt                  real headers / root tags / sample lines
    STATS.json                     counts per source
    sample_*.jsonl                 first 50 records of each (SEND THESE TO CLAUDE)

Send Claude: STRUCTURE.txt, STATS.json, and the sample_*.jsonl files.
Keep the big processed/*.jsonl on your machine for build_index.py later.
"""

import os
import re
import csv
import json
import unicodedata
from collections import defaultdict

csv.field_size_limit(10_000_000)

# ------------------------------------------------------------------ config
UN_LIMIT = 150_000          # max sentence pairs to keep per UN language pair
LEGAL_ONLY = True           # keep only legal-ish UN sentences (keyword filter)
SAMPLE_SIZE = 50            # records per sample file sent to Claude

# legal keywords (English side) used to filter the general UN corpus
LEGAL_KW = re.compile(
    r"\b(court|tribunal|judg|convention|treaty|article|provision|law|legal|"
    r"rights?|obligation|protocol|resolution|agreement|statute|jurisdiction|"
    r"party|parties|shall|hereby|whereas|pursuant|accordance|charter|"
    r"ratif|accede|sovereign|liabilit|damages|plaintiff|defendant|appeal)\b",
    re.I,
)

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+")

report = []
stats = defaultdict(dict)
samples = {}


def log(msg=""):
    print(msg)
    report.append(str(msg))


# ------------------------------------------------------------------ helpers
def find_data_dir():
    """Return the folder that actually contains the corpora."""
    here = os.getcwd()
    candidates = [here, os.path.join(here, "RAG_DATA"),
                  os.path.dirname(os.path.abspath(__file__))]
    for c in candidates:
        if not os.path.isdir(c):
            continue
        names = {n.lower() for n in os.listdir(c)}
        if "ar-en" in names or "en-fr" in names or "legis.xml" in names:
            return c
    return here


def find_entry(data_dir, wanted_lower):
    """Case-insensitive lookup of a file/folder name inside data_dir."""
    for n in os.listdir(data_dir):
        if n.lower() == wanted_lower:
            return os.path.join(data_dir, n)
    return None


def find_dir_containing(data_dir, substr):
    for n in os.listdir(data_dir):
        p = os.path.join(data_dir, n)
        if os.path.isdir(p) and substr.lower() in n.lower():
            return p
    return None


def digits(s):
    return sorted(_NUM.findall(s.translate(_ARABIC_INDIC)))


def aligned(src, tgt):
    """Cheap sanity filter for a parallel pair (mirrors the project's D13)."""
    ls, lt = len(src), len(tgt)
    if ls < 2 or lt < 2 or ls > 2000 or lt > 2000:
        return False
    ratio = lt / max(ls, 1)
    if ratio < 0.35 or ratio > 3.2:
        return False
    if digits(src) != digits(tgt):
        return False
    return True


def rec(doc_class, sl, tl, kind, source, target, jur, origin, section=""):
    return {
        "doc_class": doc_class, "src_lang": sl, "tgt_lang": tl, "kind": kind,
        "source": source, "target": target, "jurisdiction": jur,
        "section": section, "status": "seed", "origin": origin, "notes": "",
    }


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ TERMIUM
def process_termium(folder, out_dir):
    log("\n=== TERMIUM (EN<->FR terminology) ===")
    if not folder:
        log("NOT FOUND: TERMIUM folder")
        return
    csvs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".csv"))
    log(f"Folder: {os.path.basename(folder)}  ({len(csvs)} CSV files)")
    if not csvs:
        return

    records, seen = [], set()
    headers_logged = False

    # Real TERMIUM Plus open-data schema (confirmed): the actual term is in
    # TERM_EN / TERME_FR; abbreviations in ABBREVIATION_EN / ABBREVIATION_FR;
    # synonyms (semicolon-separated) in SYNONYMS_EN / SYNONYMES_FR.
    def add_pair(s, t, cf, kind="term"):
        s, t = (s or "").strip(), (t or "").strip()
        if not s or not t or len(s) > 200 or len(t) > 200:
            return
        key = (s.lower(), t.lower())
        if key in seen:
            return
        seen.add(key)
        records.append(rec("common", "en", "fr", kind, s, t, "CA", f"termium/{cf}"))

    for cf in csvs:
        path = os.path.join(folder, cf)
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames:
                    continue
                if not headers_logged:
                    log("Using TERM_EN -> TERME_FR (plus abbreviations & aligned synonyms)")
                    headers_logged = True
                for row in reader:
                    add_pair(row.get("TERM_EN"), row.get("TERME_FR"), cf)
                    add_pair(row.get("ABBREVIATION_EN"), row.get("ABBREVIATION_FR"), cf)
                    # synonyms: pair positionally only when counts match
                    syn_en = [x.strip() for x in (row.get("SYNONYMS_EN") or "").split(";") if x.strip()]
                    syn_fr = [x.strip() for x in (row.get("SYNONYMES_FR") or "").split(";") if x.strip()]
                    if syn_en and len(syn_en) == len(syn_fr):
                        for a, b in zip(syn_en, syn_fr):
                            add_pair(a, b, cf)
        except Exception as e:
            log(f"  ERROR in {cf}: {e}")

    write_jsonl(os.path.join(out_dir, "processed", "termium.jsonl"), records)
    samples["termium"] = records[:SAMPLE_SIZE]
    stats["termium"] = {"records": len(records), "files": len(csvs)}
    log(f"  -> {len(records)} unique term pairs written")


# ------------------------------------------------------------------ legis.xml
def process_legis(xml_path, out_dir):
    log("\n=== Justice Laws (EN<->FR Act titles) ===")
    if not xml_path:
        log("NOT FOUND: legis.xml")
        return
    import xml.etree.ElementTree as ET
    with open(xml_path, "rb") as f:
        raw = f.read()
    root = None
    # Detect encoding from BOM, then try a few decodings
    attempts = []
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        attempts.append("utf-16")
    attempts += ["utf-8-sig", "utf-8", "utf-16", "latin-1"]
    for enc in attempts:
        try:
            text = raw.decode(enc, errors="strict")
            # strip anything before the first '<'
            lt = text.find("<")
            if lt > 0:
                text = text[lt:]
            root = ET.fromstring(text)
            log(f"Parsed with encoding: {enc}")
            break
        except Exception:
            continue
    if root is None:
        log("ERROR parsing XML: could not decode/parse (tried " + ", ".join(attempts) + ")")
        return

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    by_uid = defaultdict(dict)  # uid -> {lang: title}
    acts = 0
    for el in root.iter():
        if local(el.tag) != "Act":
            continue
        acts += 1
        uid = lang = title = ""
        for child in el:
            lt = local(child.tag)
            if lt == "UniqueId":
                uid = (child.text or "").strip()
            elif lt == "Language":
                lang = (child.text or "").strip().lower()
            elif lt == "Title":
                title = (child.text or "").strip()
        if uid and lang and title:
            key = "en" if "eng" in lang else ("fr" if "fra" in lang else lang)
            by_uid[uid][key] = title

    records = []
    for uid, d in by_uid.items():
        if "en" in d and "fr" in d:
            records.append(rec("legislation", "en", "fr", "term",
                               d["en"], d["fr"], "CA", "justice_laws/legis.xml", "procedure"))

    write_jsonl(os.path.join(out_dir, "processed", "legislation.jsonl"), records)
    samples["legislation"] = records[:SAMPLE_SIZE]
    stats["legislation"] = {"acts_seen": acts, "paired_titles": len(records)}
    log(f"  -> {acts} Act entries, {len(records)} EN/FR title pairs written")


# ------------------------------------------------------------------ UN corpus
def process_un(folder, lang1, lang2, out_name, out_dir):
    log(f"\n=== UN Parallel Corpus ({lang1}->{lang2}) ===")
    if not folder or not os.path.isdir(folder):
        log(f"NOT FOUND: {out_name} folder")
        return
    src_file = tgt_file = None
    for f in os.listdir(folder):
        if f.endswith("." + lang1):
            src_file = os.path.join(folder, f)
        if f.endswith("." + lang2):
            tgt_file = os.path.join(folder, f)
    if not src_file or not tgt_file:
        log(f"  Missing .{lang1} or .{lang2} file in {folder}")
        return
    log(f"  src: {os.path.basename(src_file)}  tgt: {os.path.basename(tgt_file)}")

    records = []
    raw = kept = 0
    first_lines = []
    en_side = lang1 if lang1 == "en" else (lang2 if lang2 == "en" else None)

    with open(src_file, encoding="utf-8", errors="replace") as sf, \
         open(tgt_file, encoding="utf-8", errors="replace") as tf:
        for s, t in zip(sf, tf):
            raw += 1
            s, t = s.strip(), t.strip()
            if len(first_lines) < 3:
                first_lines.append(f"  [{lang1}] {s[:90]}\n  [{lang2}] {t[:90]}")
            if not s or not t:
                continue
            if not aligned(s, t):
                continue
            if LEGAL_ONLY and en_side:
                probe = s if en_side == lang1 else t
                if not LEGAL_KW.search(probe):
                    continue
            records.append(rec("common", lang1, lang2, "sentence", s, t, "UN", "un_corpus_v1.0"))
            kept += 1
            if kept >= UN_LIMIT:
                break

    write_jsonl(os.path.join(out_dir, "processed", out_name + ".jsonl"), records)
    samples[out_name] = records[:SAMPLE_SIZE]
    stats[out_name] = {"lines_read": raw, "kept": kept, "legal_only": LEGAL_ONLY}
    log("  first lines:\n" + "\n".join(first_lines))
    log(f"  -> read {raw}, kept {kept} pairs written")


# ------------------------------------------------------------------ main
def main():
    import sys
    # Optional: limit which sources run, e.g.  python rag_data_processor.py termium legis
    targets = {a.lower() for a in sys.argv[1:]} or {"termium", "legis", "un"}

    data_dir = find_data_dir()
    out_dir = os.path.join(data_dir, "rag_output")
    os.makedirs(out_dir, exist_ok=True)

    log("=" * 60)
    log("RAG Data Processor v4")
    log("=" * 60)
    log(f"Data folder:   {data_dir}")
    log(f"Output folder: {out_dir}")
    log(f"Running: {sorted(targets)}")

    termium = find_dir_containing(data_dir, "droitjustice") or find_dir_containing(data_dir, "law")
    legis = find_entry(data_dir, "legis.xml")
    ar_en = find_entry(data_dir, "ar-en")
    en_fr = find_entry(data_dir, "en-fr")

    if "termium" in targets:
        process_termium(termium, out_dir)
    if "legis" in targets:
        process_legis(legis, out_dir)
    if "un" in targets:
        process_un(ar_en, "ar", "en", "un_ar_en", out_dir)
        process_un(en_fr, "en", "fr", "un_en_fr", out_dir)

    # summary
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    total = sum(v.get("records", v.get("kept", v.get("paired_titles", 0))) for v in stats.values())
    for k, v in stats.items():
        log(f"  {k}: {v}")
    log(f"  TOTAL records: {total}")

    # write reports + samples
    with open(os.path.join(out_dir, "STRUCTURE.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    with open(os.path.join(out_dir, "STATS.json"), "w", encoding="utf-8") as f:
        json.dump(dict(stats), f, indent=2, ensure_ascii=False)
    for name, recs in samples.items():
        if recs:
            with open(os.path.join(out_dir, f"sample_{name}.jsonl"), "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    log("\nDONE. Send Claude these files from the rag_output folder:")
    log("  - STRUCTURE.txt")
    log("  - STATS.json")
    log("  - sample_*.jsonl  (all of them)")
    log("Keep processed/*.jsonl on your machine for build_index.py.")

    with open(os.path.join(out_dir, "STRUCTURE.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))


if __name__ == "__main__":
    main()
