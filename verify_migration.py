"""
Verification: does Zilliz return the SAME passages as your local ChromaDB?

This is the step that catches a silent migration bug before it reaches
production. It takes real passages out of your local index, runs each one
through BOTH backends with identical inputs, and compares what comes back.

A note on what "same" means here: both Chroma (HNSW) and Milvus (AUTOINDEX) are
APPROXIMATE nearest-neighbour indexes. Small ordering differences on near-ties
are normal and harmless. What matters is high overlap and a matching top hit.

    >=90% mean overlap  -> healthy, migrate with confidence
    70-90%              -> usually index-approximation noise; check the samples
    <70%                -> something is wrong (filter, schema or threshold)

RUN (PowerShell, venv313 active, from the project root):

    $env:ZILLIZ_URI   = "https://in03-....aws-eu-central-1.cloud.zilliz.com"
    $env:ZILLIZ_TOKEN = "<your api key>"
    python verify_migration.py

Optional:  $env:VERIFY_SAMPLES = "40"
"""
import os
import random
import sys

SAMPLES = int(os.getenv("VERIFY_SAMPLES", "25"))
POOL = int(os.getenv("VERIFY_POOL", "3000"))

if not (os.getenv("ZILLIZ_URI") and os.getenv("ZILLIZ_TOKEN")):
    sys.exit("Set ZILLIZ_URI and ZILLIZ_TOKEN first (see header).")

import rag
from config import settings

if not rag.use_zilliz():
    sys.exit("rag.use_zilliz() is False -- config.py did not pick up the env vars.")

print("=" * 68)
print("MIGRATION VERIFICATION  (local Chroma  vs  Zilliz)")
print("=" * 68)
print(f"  collection : {settings.zilliz_collection}")
print(f"  threshold  : max_example_distance = {settings.max_example_distance}")

# Pull a pool of real records from the LOCAL store to use as queries.
print(f"\nSampling {SAMPLES} query passages from the local index ...")
col = rag.collection()
pool = col.get(limit=POOL, include=["documents", "metadatas"])
docs, metas = pool["documents"], pool["metadatas"]

candidates = [
    (d, m) for d, m in zip(docs, metas)
    if d and m and m.get("src_lang") and m.get("tgt_lang") and m.get("doc_class")
]
if not candidates:
    sys.exit("No usable sample records found in the local index.")

random.seed(42)  # deterministic: same samples every run
picks = random.sample(candidates, min(SAMPLES, len(candidates)))

overlaps = []
top1_hits = 0
both_empty = 0
only_chroma = 0
only_zilliz = 0

print(f"Comparing {len(picks)} queries across both backends ...\n")
for i, (doc, meta) in enumerate(picks, 1):
    src = meta.get("src_lang", "")
    tgt = meta.get("tgt_lang", "")
    dc = meta.get("doc_class", "")
    jur = meta.get("jurisdiction", "") or ""
    sec = meta.get("section", "") or ""

    a = rag._find_examples_chroma(doc, dc, src, tgt, jur, sec)
    b = rag._find_examples_zilliz(doc, dc, src, tgt, jur, sec)

    sa = {x[0] for x in a}
    sb = {x[0] for x in b}

    if not sa and not sb:
        both_empty += 1
        status = "both empty (filter matched nothing) - OK"
        ov = 1.0
    elif not sb:
        only_chroma += 1
        status = "!! ZILLIZ RETURNED NOTHING"
        ov = 0.0
    elif not sa:
        only_zilliz += 1
        status = "chroma empty, zilliz returned results"
        ov = 0.0
    else:
        ov = len(sa & sb) / len(sa | sb)
        same_top = a[0][0] == b[0][0]
        top1_hits += 1 if same_top else 0
        status = f"overlap {ov*100:5.1f}%  top-1 {'MATCH' if same_top else 'differs'}"

    overlaps.append(ov)
    print(f"  [{i:>2}] {src}->{tgt} {dc:<18} chroma={len(sa):>2} zilliz={len(sb):>2}  {status}")

n_compared = len(picks) - both_empty - only_chroma - only_zilliz
mean_ov = sum(overlaps) / len(overlaps) if overlaps else 0

print("\n" + "=" * 68)
print("RESULT")
print("=" * 68)
print(f"  queries run          : {len(picks)}")
print(f"  both returned results: {n_compared}")
print(f"  both empty           : {both_empty}")
print(f"  zilliz empty only    : {only_chroma}   <-- must be 0")
print(f"  mean overlap         : {mean_ov*100:.1f}%")
if n_compared:
    print(f"  top-1 agreement      : {top1_hits}/{n_compared} ({top1_hits/n_compared*100:.0f}%)")

print()
if only_chroma:
    print("  FAIL: Zilliz returned nothing where Chroma did. Likely the filter")
    print("        expression or a missing/renamed scalar field. Do not cut over.")
elif mean_ov >= 0.9:
    print("  PASS: retrieval matches. Safe to deploy against Zilliz.")
elif mean_ov >= 0.7:
    print("  LIKELY OK: differences look like ANN approximation noise.")
    print("        Skim the per-query lines above before cutting over.")
else:
    print("  FAIL: overlap too low. Do not cut over -- paste this output back.")