"""
Zilliz free-tier PROBE -- load a 10,000-record sample and measure the cost
BEFORE committing the full 562,310-record migration.

Why: Zilliz's free tier gives 2.5M vCU/month but does NOT publish how many vCU
an insert costs. This loads 1.8% of your data so you can read the real vCU off
the Zilliz console, multiply by ~56, and confirm the full load fits -- instead
of finding out mid-migration (which is exactly how Pinecone's free tier failed).

It does NOT touch your local chroma_db except to READ from it. It creates a
throwaway collection "probe_10k" on Zilliz that you drop afterwards.

RUN (PowerShell, venv313 active, from the project root where chroma_db/ lives):

    pip install "pymilvus>=2.4"
    $env:ZILLIZ_URI   = "https://in03-....aws-eu-central-1.cloud.zilliz.com"
    $env:ZILLIZ_TOKEN = "<your api key>"
    python zilliz_probe.py

Then open the Zilliz console -> your cluster -> Usage / Monitoring, note the
vCU consumed, and paste it back.
"""
import os
import sys
import time

ZILLIZ_URI = os.getenv("ZILLIZ_URI", "").strip()
ZILLIZ_TOKEN = os.getenv("ZILLIZ_TOKEN", "").strip()
CHROMA_DIR = os.getenv("CHROMA_DIR", "./chroma_db")
SOURCE_COLLECTION = os.getenv("SOURCE_COLLECTION", "legal_tm")
PROBE_COLLECTION = "probe_10k"
SAMPLE = int(os.getenv("PROBE_SAMPLE", "10000"))
BATCH = int(os.getenv("PROBE_BATCH", "1000"))
DIM = 768

# Explicit (indexed) scalar fields = exactly the ones find_examples() filters on.
# Everything else (target, document, jurisdiction, section) rides in Milvus's
# dynamic field, so we don't have to predeclare the whole schema.
FILTER_FIELDS = ("src_lang", "tgt_lang", "doc_class", "status")
DYNAMIC_FIELDS = ("target", "document", "jurisdiction", "section")

if not ZILLIZ_URI or not ZILLIZ_TOKEN:
    sys.exit("Set ZILLIZ_URI and ZILLIZ_TOKEN environment variables first (see header).")

if "--drop" in sys.argv:
    from pymilvus import MilvusClient
    c = MilvusClient(uri=ZILLIZ_URI, token=ZILLIZ_TOKEN)
    if c.has_collection(PROBE_COLLECTION):
        c.drop_collection(PROBE_COLLECTION)
        print(f"Dropped '{PROBE_COLLECTION}'.")
    else:
        print(f"'{PROBE_COLLECTION}' not found -- nothing to drop.")
    sys.exit(0)


def _as_floats(vec):
    tl = getattr(vec, "tolist", None)
    return tl() if tl else [float(x) for x in vec]


def _s(v):
    return "" if v is None else str(v)


print("=" * 64)
print("ZILLIZ FREE-TIER PROBE")
print("=" * 64)

# --- 1. Read a sample out of the local Chroma store -----------------------
print(f"\n[1/4] Reading {SAMPLE:,} records from local Chroma ({CHROMA_DIR}) ...")
import chromadb

cc = chromadb.PersistentClient(path=CHROMA_DIR)
col = cc.get_collection(SOURCE_COLLECTION)
total = col.count()
print(f"      source collection '{SOURCE_COLLECTION}': {total:,} records")

res = col.get(limit=SAMPLE, include=["embeddings", "documents", "metadatas"])
ids = res["ids"]
embs = res["embeddings"]
docs = res["documents"] or [""] * len(ids)
metas = res["metadatas"] or [{}] * len(ids)
got = len(ids)
print(f"      pulled {got:,} records (with embeddings)")

if got == 0:
    sys.exit("No records returned from Chroma -- check CHROMA_DIR / collection name.")

# Measure average serialized size so we can sanity-check storage later.
sample_meta = metas[0] if metas else {}
print(f"      metadata keys present: {sorted(sample_meta.keys())}")

# --- 2. Connect to Zilliz and (re)create the probe collection -------------
print(f"\n[2/4] Connecting to Zilliz and creating '{PROBE_COLLECTION}' ...")
from pymilvus import MilvusClient, DataType

client = MilvusClient(uri=ZILLIZ_URI, token=ZILLIZ_TOKEN)

if client.has_collection(PROBE_COLLECTION):
    client.drop_collection(PROBE_COLLECTION)
    print("      dropped pre-existing probe collection")

schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=128)
schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
schema.add_field("src_lang", DataType.VARCHAR, max_length=16)
schema.add_field("tgt_lang", DataType.VARCHAR, max_length=16)
schema.add_field("doc_class", DataType.VARCHAR, max_length=64)
schema.add_field("status", DataType.VARCHAR, max_length=32)

index_params = client.prepare_index_params()
index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")

client.create_collection(
    collection_name=PROBE_COLLECTION,
    schema=schema,
    index_params=index_params,
)
print("      collection created (COSINE, AUTOINDEX, dynamic field on)")

# --- 3. Insert in batches -------------------------------------------------
print(f"\n[3/4] Inserting {got:,} records in batches of {BATCH} ...")
t0 = time.time()
inserted = 0
approx_bytes = 0
for start in range(0, got, BATCH):
    rows = []
    for i in range(start, min(start + BATCH, got)):
        m = metas[i] or {}
        row = {
            "pk": _s(ids[i])[:128],
            "vector": _as_floats(embs[i]),
        }
        for f in FILTER_FIELDS:
            row[f] = _s(m.get(f))
        row["document"] = _s(docs[i])
        for f in ("target", "jurisdiction", "section"):
            row[f] = _s(m.get(f))
        approx_bytes += len(row["document"]) + len(row["target"]) + DIM * 4 + 80
        rows.append(row)
    client.insert(collection_name=PROBE_COLLECTION, data=rows)
    inserted += len(rows)
    print(f"      {inserted:,}/{got:,}", end="\r")
elapsed = time.time() - t0
print(f"\n      inserted {inserted:,} in {elapsed:.1f}s")

# --- 4. One test query (same shape find_examples uses) --------------------
print("\n[4/4] Running one test query (nq=1, topK=12, with scalar filter) ...")
qv = _as_floats(embs[0])
expr = 'src_lang == "ar" && tgt_lang == "en" && doc_class in ["contract", "common"]'
try:
    hit = client.search(
        collection_name=PROBE_COLLECTION,
        data=[qv],
        limit=12,
        filter=expr,
        output_fields=["target", "doc_class", "status"],
        search_params={"metric_type": "COSINE"},
    )
    print(f"      query OK -- {len(hit[0])} hits returned under that filter")
except Exception as e:
    print(f"      query raised: {e}")
    print("      (not fatal for the probe -- filter just may match few rows in this sample)")

avg_kb = (approx_bytes / inserted) / 1024 if inserted else 0
print("\n" + "=" * 64)
print("PROBE COMPLETE")
print("=" * 64)
print(f"  Records loaded      : {inserted:,}  ({inserted/total*100:.1f}% of {total:,})")
print(f"  Avg record size     : ~{avg_kb:.2f} KB  -> full set ~{avg_kb*total/1024/1024:.2f} GB")
print(f"  Local insert time   : {elapsed:.1f}s  -> full set ~{elapsed*total/inserted/60:.1f} min")
print()
print("  NEXT: open Zilliz console -> your cluster -> Usage / Monitoring.")
print("  Read the vCU consumed by this run and paste it back.")
print(f"  Rule of thumb: probe_vCU x {total/inserted:.0f} must stay under 2.5M.")
print()
print(f"  When done, free the slot with:  python zilliz_probe.py --drop")