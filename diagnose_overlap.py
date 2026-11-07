"""
Diagnostic: is the Chroma/Zilliz overlap gap harmless near-tie noise, or real
recall loss?

verify_migration.py compares WHICH passages came back. That is the wrong question
on its own: with a legal corpus full of near-duplicate sentences, many candidates
sit at nearly identical distances, so which ones surface is close to a coin flip
and the examples are equally good either way.

The right question is HOW CLOSE the returned passages are. This prints the actual
distances side by side.

  Zilliz mean distance ~= Chroma mean distance  -> near-tie noise, retrieval is fine
  Zilliz mean distance clearly worse            -> real recall loss, needs fixing

It also re-runs Zilliz with a higher AUTOINDEX recall level to see whether that
closes the gap.

RUN (same env vars as before, from the project root):

    python diagnose_overlap.py
"""
import os
import random
import sys

if not (os.getenv("ZILLIZ_URI") and os.getenv("ZILLIZ_TOKEN")):
    sys.exit("Set ZILLIZ_URI and ZILLIZ_TOKEN first.")

import rag
from config import settings

SAMPLES = int(os.getenv("VERIFY_SAMPLES", "25"))
POOL = int(os.getenv("VERIFY_POOL", "3000"))
K = settings.top_k_examples


def chroma_raw(chunk, dc, src, tgt):
    where = {"$and": [
        {"src_lang": src}, {"tgt_lang": tgt},
        {"doc_class": {"$in": [dc, "common"]}},
        {"status": {"$in": list(settings.allowed_status)}},
    ]}
    r = rag.collection().query(
        query_embeddings=rag.embed([chunk[:1000]], query=True),
        n_results=K * 3, where=where,
        include=["documents", "distances"],
    )
    return list(zip(r["documents"][0], r["distances"][0]))


def zilliz_raw(chunk, dc, src, tgt, level=None):
    sp = {"metric_type": "COSINE"}
    if level is not None:
        sp["params"] = {"level": level}
    r = rag._milvus().search(
        collection_name=settings.zilliz_collection,
        data=rag.embed([chunk[:1000]], query=True),
        limit=K * 3,
        filter=rag._expr(dc, src, tgt),
        output_fields=["document"],
        search_params=sp,
    )
    if not r or not r[0]:
        return []
    return [((h.get("entity", {}) or {}).get("document", ""),
             1.0 - float(h.get("distance", 0.0))) for h in r[0]]


def keep(pairs):
    """Apply the same 0.22 cut-off both backends use, take top K."""
    out = [(d, dist) for d, dist in pairs if dist <= settings.max_example_distance]
    out.sort(key=lambda x: x[1])
    return out[:K]


print("=" * 72)
print("OVERLAP DIAGNOSTIC -- near-tie noise or real recall loss?")
print("=" * 72)

col = rag.collection()
pool = col.get(limit=POOL, include=["documents", "metadatas"])
cands = [(d, m) for d, m in zip(pool["documents"], pool["metadatas"])
         if d and m and m.get("src_lang") and m.get("tgt_lang") and m.get("doc_class")]
random.seed(42)  # same samples as verify_migration.py
picks = random.sample(cands, min(SAMPLES, len(cands)))

c_means, z_means, z_hi_means = [], [], []
ov_base, ov_hi = [], []
worst = []

for i, (doc, meta) in enumerate(picks, 1):
    src, tgt = meta.get("src_lang", ""), meta.get("tgt_lang", "")
    dc = meta.get("doc_class", "")

    c = keep(chroma_raw(doc, dc, src, tgt))
    z = keep(zilliz_raw(doc, dc, src, tgt))
    try:
        zh = keep(zilliz_raw(doc, dc, src, tgt, level=5))
    except Exception:
        zh = z  # level param unsupported -> ignore this column

    sc, sz, szh = {x[0] for x in c}, {x[0] for x in z}, {x[0] for x in zh}
    o = len(sc & sz) / len(sc | sz) if (sc or sz) else 1.0
    oh = len(sc & szh) / len(sc | szh) if (sc or szh) else 1.0
    ov_base.append(o)
    ov_hi.append(oh)

    cm = sum(d for _, d in c) / len(c) if c else 0
    zm = sum(d for _, d in z) / len(z) if z else 0
    zhm = sum(d for _, d in zh) / len(zh) if zh else 0
    if c: c_means.append(cm)
    if z: z_means.append(zm)
    if zh: z_hi_means.append(zhm)

    flag = "  <-- LOW" if o < 0.5 else ""
    print(f"\n[{i:>2}] {src}->{tgt}  overlap {o*100:5.1f}% (level5: {oh*100:5.1f}%){flag}")
    print(f"     chroma dists: {[round(d,4) for _, d in c]}  mean {cm:.4f}")
    print(f"     zilliz dists: {[round(d,4) for _, d in z]}  mean {zm:.4f}")
    if o < 0.5:
        worst.append((i, cm, zm))

print("\n" + "=" * 72)
print("SUMMARY")
print("=" * 72)
mc = sum(c_means)/len(c_means) if c_means else 0
mz = sum(z_means)/len(z_means) if z_means else 0
mzh = sum(z_hi_means)/len(z_hi_means) if z_hi_means else 0
print(f"  mean returned distance  chroma : {mc:.4f}")
print(f"  mean returned distance  zilliz : {mz:.4f}   (delta {mz-mc:+.4f})")
print(f"  mean returned distance  lvl 5  : {mzh:.4f}   (delta {mzh-mc:+.4f})")
print(f"  mean overlap            base   : {sum(ov_base)/len(ov_base)*100:.1f}%")
print(f"  mean overlap            lvl 5  : {sum(ov_hi)/len(ov_hi)*100:.1f}%")

if worst:
    print(f"\n  low-overlap queries ({len(worst)}):")
    for i, cm, zm in worst:
        verdict = "same quality" if abs(zm - cm) < 0.02 else "ZILLIZ WORSE"
        print(f"    [{i:>2}] chroma mean {cm:.4f}  zilliz mean {zm:.4f}  -> {verdict}")

print()
if abs(mz - mc) < 0.02:
    print("  VERDICT: distances match. The overlap gap is near-tie shuffling between")
    print("           equally-good passages. Retrieval quality is preserved -- safe to deploy.")
elif mz > mc:
    print("  VERDICT: Zilliz returns measurably more distant passages -> real recall loss.")
    print("           If the level-5 column is better, we raise the search level in rag.py.")
else:
    print("  VERDICT: Zilliz returns CLOSER passages than Chroma. Also fine.")