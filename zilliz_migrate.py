"""
Full migration: local ChromaDB  ->  Zilliz Cloud (Milvus).

Moves all 562,310 translation-memory records. It does NOT re-embed anything --
the existing vectors are copied byte-for-byte, so your tuned 0.22 distance
threshold keeps its exact meaning.

Your local chroma_db is only ever READ. Nothing is modified or deleted.

RESUMABLE: progress is checkpointed after every batch. If the connection drops,
re-run the same command and it continues from where it stopped. On resume it
first deletes the batch it was mid-way through, so records are never duplicated
(Milvus does NOT enforce primary-key uniqueness on insert -- a naive retry would
silently create duplicates and corrupt retrieval).

RUN (PowerShell, venv313 active, from the project root):

    $env:ZILLIZ_URI   = "https://in03-....aws-eu-central-1.cloud.zilliz.com"
    $env:ZILLIZ_TOKEN = "<your api key>"
    python zilliz_migrate.py

Flags:
    --reset   drop the target collection and start over from zero
    --status  print progress/counts and exit
"""
import json
import os
import sys
import time

ZILLIZ_URI = os.getenv("ZILLIZ_URI", "").strip()
ZILLIZ_TOKEN = os.getenv("ZILLIZ_TOKEN", "").strip()
CHROMA_DIR = os.getenv("CHROMA_DIR", "./chroma_db")
SOURCE_COLLECTION = os.getenv("SOURCE_COLLECTION", "legal_tm")
TARGET_COLLECTION = os.getenv("ZILLIZ_COLLECTION", "legal_tm")
BATCH = int(os.getenv("MIGRATE_BATCH", "1000"))
STATE_FILE = ".zilliz_migrate_state.json"
DIM = 768

# Indexed scalar fields == exactly what find_examples() filters on.
FILTER_FIELDS = ("src_lang", "tgt_lang", "doc_class", "status")
# Everything else rides in Milvus's dynamic field (no length caps to trip over).
PAYLOAD_FIELDS = ("target", "jurisdiction", "section", "origin")

if not ZILLIZ_URI or not ZILLIZ_TOKEN:
    sys.exit("Set ZILLIZ_URI and ZILLIZ_TOKEN first (see header).")

from pymilvus import MilvusClient, DataType


def _as_floats(vec):
    tl = getattr(vec, "tolist", None)
    return tl() if tl else [float(x) for x in vec]


def _s(v):
    return "" if v is None else str(v)


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"done": 0}


def _save_state(done):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"done": done, "collection": TARGET_COLLECTION}, fh)
    os.replace(tmp, STATE_FILE)


def _all_ids(col):
    """Stable, ordered id list. Avoids SQLite OFFSET scans at high offsets."""
    try:
        return col.get(include=[])["ids"]
    except Exception:
        return col.get()["ids"]


client = MilvusClient(uri=ZILLIZ_URI, token=ZILLIZ_TOKEN)

if "--status" in sys.argv:
    st = _load_state()
    n = client.get_collection_stats(TARGET_COLLECTION)["row_count"] if client.has_collection(TARGET_COLLECTION) else 0
    print(f"checkpoint: {st.get('done', 0):,} processed")
    print(f"zilliz '{TARGET_COLLECTION}': {n:,} rows")
    sys.exit(0)

print("=" * 64)
print("CHROMA -> ZILLIZ MIGRATION")
print("=" * 64)

# --- source ---------------------------------------------------------------
import chromadb

cc = chromadb.PersistentClient(path=CHROMA_DIR)
col = cc.get_collection(SOURCE_COLLECTION)
total = col.count()
print(f"\nSource : {CHROMA_DIR} :: '{SOURCE_COLLECTION}'  ({total:,} records)")
print(f"Target : {TARGET_COLLECTION} @ Zilliz")

# --- target collection ----------------------------------------------------
if "--reset" in sys.argv and client.has_collection(TARGET_COLLECTION):
    client.drop_collection(TARGET_COLLECTION)
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    print("\n--reset: dropped target collection and cleared checkpoint")

if not client.has_collection(TARGET_COLLECTION):
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=128)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("src_lang", DataType.VARCHAR, max_length=16)
    schema.add_field("tgt_lang", DataType.VARCHAR, max_length=16)
    schema.add_field("doc_class", DataType.VARCHAR, max_length=64)
    schema.add_field("status", DataType.VARCHAR, max_length=32)

    idx = client.prepare_index_params()
    idx.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(TARGET_COLLECTION, schema=schema, index_params=idx)
    print("Created target collection (COSINE / AUTOINDEX / dynamic field on)")

# --- plan -----------------------------------------------------------------
print("\nListing source ids ...")
ids_all = _all_ids(col)
print(f"  {len(ids_all):,} ids")

state = _load_state()
done = int(state.get("done", 0))
if done and state.get("collection") != TARGET_COLLECTION:
    done = 0  # checkpoint belongs to a different target
if done:
    print(f"\nRESUMING from checkpoint: {done:,} already processed")
else:
    print("\nStarting from the beginning")

t0 = time.time()
first_batch_of_run = True

for start in range(done, len(ids_all), BATCH):
    chunk_ids = ids_all[start:start + BATCH]

    # On resume, the batch we died inside may be half-written. Delete it first
    # so the re-insert cannot create duplicate primary keys.
    if first_batch_of_run and start > 0:
        try:
            client.delete(collection_name=TARGET_COLLECTION, ids=chunk_ids)
            print(f"  cleaned partial batch at offset {start:,}")
        except Exception as e:
            print(f"  (cleanup skipped: {e})")
    first_batch_of_run = False

    got = col.get(ids=chunk_ids, include=["embeddings", "documents", "metadatas"])
    g_ids = got["ids"]
    g_emb = got["embeddings"]
    g_doc = got["documents"] or [""] * len(g_ids)
    g_met = got["metadatas"] or [{}] * len(g_ids)

    rows = []
    for i in range(len(g_ids)):
        m = g_met[i] or {}
        row = {"pk": _s(g_ids[i])[:128], "vector": _as_floats(g_emb[i])}
        for f in FILTER_FIELDS:
            row[f] = _s(m.get(f))
        row["document"] = _s(g_doc[i])
        for f in PAYLOAD_FIELDS:
            row[f] = _s(m.get(f))
        rows.append(row)

    # retry on transient rate-limit / network blips
    for attempt in range(5):
        try:
            client.insert(collection_name=TARGET_COLLECTION, data=rows)
            break
        except Exception as e:
            if attempt == 4:
                _save_state(start)
                sys.exit(f"\nFailed at offset {start:,} after 5 attempts: {e}\n"
                         f"Re-run the same command to resume from here.")
            wait = 2 ** attempt
            print(f"\n  insert failed ({e}); retrying in {wait}s ...")
            time.sleep(wait)

    processed = start + len(rows)
    _save_state(processed)

    el = time.time() - t0
    rate = (processed - done) / el if el > 0 else 0
    eta = (len(ids_all) - processed) / rate / 60 if rate > 0 else 0
    pct = processed / len(ids_all) * 100
    print(f"  {processed:,}/{len(ids_all):,} ({pct:5.1f}%)  {rate:,.0f} rec/s  ETA {eta:4.1f} min", end="\r")

print("\n\nFlushing ...")
try:
    client.flush(TARGET_COLLECTION)
except Exception:
    pass
time.sleep(3)

n = client.get_collection_stats(TARGET_COLLECTION)["row_count"]
print("=" * 64)
print("MIGRATION COMPLETE")
print("=" * 64)
print(f"  Source records : {total:,}")
print(f"  Zilliz rows    : {n:,}")
print(f"  Elapsed        : {(time.time()-t0)/60:.1f} min")
if n < total:
    print(f"\n  NOTE: row_count can lag a few seconds after flush. Re-check with:")
    print(f"        python zilliz_migrate.py --status")
print("\n  NEXT: python verify_migration.py")