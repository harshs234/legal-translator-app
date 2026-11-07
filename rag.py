"""Retrieval layer.
  - Terminology: exact n-gram lookup in SQLite (termstore.py)
  - Translation memory (aligned sentences/paragraphs): semantic search
Everything is filtered by language pair, document class (+ "common"), and record status.

Two interchangeable backends, chosen at runtime:
  * Zilliz Cloud (Milvus)  -- when ZILLIZ_URI + ZILLIZ_TOKEN are set. This is what
    the deployed Streamlit app uses: the 1.6 GB vector index lives in the cloud,
    so the app stays well under Streamlit's 2.7 GB memory cap.
  * Local ChromaDB         -- when they are not set. Unchanged from before, so
    build_index.py, the test suite and offline development keep working as-is.

Both paths return identical results: Milvus reports cosine SIMILARITY while Chroma
reports cosine DISTANCE, so the Milvus score is converted back with
distance = 1 - similarity before any thresholding. settings.max_example_distance
(0.22) therefore keeps exactly the meaning it was tuned with.
"""
import functools
import json
import math
import zlib
from config import settings
import termstore


# --------------------------------------------------------------------------
# Embeddings (identical on both backends -- same model, same vectors)
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _embedder():
    from sentence_transformers import SentenceTransformer  # lazy: heavy import
    return SentenceTransformer(settings.embed_model)


def _hash_vec(text: str, dim: int = 256):
    """Deterministic char-trigram embedding. For tests only (EMBED_BACKEND=hash)."""
    v = [0.0] * dim
    t = f"  {text.lower()}  "
    for i in range(len(t) - 2):
        v[zlib.crc32(t[i:i + 3].encode("utf-8")) % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts, query=False):
    if settings.embed_backend == "hash":
        return [_hash_vec(t) for t in texts]
    prefix = "query: " if query else "passage: "
    return _embedder().encode([prefix + t for t in texts], normalize_embeddings=True).tolist()


def use_zilliz() -> bool:
    return bool(settings.zilliz_uri and settings.zilliz_token)


# --------------------------------------------------------------------------
# Backend: Zilliz Cloud (Milvus)
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _milvus():
    from pymilvus import MilvusClient  # lazy
    return MilvusClient(uri=settings.zilliz_uri, token=settings.zilliz_token)


def _expr(doc_class, src, tgt):
    """Milvus boolean expression equivalent to the Chroma $and/$in filter.

    json.dumps is used for every interpolated value so quoting/escaping is handled
    and nothing from upstream can break out of the expression.
    """
    classes = [doc_class, "common"]
    statuses = list(settings.allowed_status)
    return (
        f"src_lang == {json.dumps(src)} && tgt_lang == {json.dumps(tgt)} "
        f"&& doc_class in {json.dumps(classes)} "
        f"&& status in {json.dumps(statuses)}"
    )


def _find_examples_zilliz(chunk, doc_class, src, tgt, jurisdiction="", section="", k=None):
    k = k or settings.top_k_examples
    try:
        res = _milvus().search(
            collection_name=settings.zilliz_collection,
            data=embed([chunk[:1000]], query=True),
            limit=k * 3,
            filter=_expr(doc_class, src, tgt),
            output_fields=["document", "target", "jurisdiction", "section", "doc_class"],
            search_params={"metric_type": "COSINE"},
        )
    except Exception:
        return []
    if not res or not res[0]:
        return []

    scored = []
    for hit in res[0]:
        ent = hit.get("entity", {}) or {}
        # Milvus COSINE returns similarity (1.0 == identical).
        # Chroma returned distance. Convert so the 0.22 cut-off is unchanged.
        dist = 1.0 - float(hit.get("distance", 0.0))
        if dist > settings.max_example_distance:
            continue
        bonus = 0.0
        if jurisdiction and ent.get("jurisdiction") == jurisdiction:
            bonus += 0.03
        if section and ent.get("section") == section:
            bonus += 0.03
        if ent.get("doc_class") == doc_class:
            bonus += 0.02
        scored.append((dist - bonus, ent.get("document", ""), ent.get("target", "")))
    scored.sort(key=lambda x: x[0])
    return [(d, t) for _, d, t in scored[:k]]


# --------------------------------------------------------------------------
# Backend: local ChromaDB (unchanged behaviour)
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def collection():
    import chromadb  # lazy: not installed in the deployed app
    client = chromadb.PersistentClient(path=settings.chroma_dir)
    return client.get_or_create_collection("legal_tm", metadata={"hnsw:space": "cosine"})


def _find_examples_chroma(chunk, doc_class, src, tgt, jurisdiction="", section="", k=None):
    k = k or settings.top_k_examples
    where = {"$and": [
        {"src_lang": src}, {"tgt_lang": tgt},
        {"doc_class": {"$in": [doc_class, "common"]}},
        {"status": {"$in": list(settings.allowed_status)}},
    ]}
    try:
        res = collection().query(query_embeddings=embed([chunk[:1000]], query=True), n_results=k * 3,
                                 where=where, include=["documents", "metadatas", "distances"])
    except Exception:
        return []
    scored = []
    for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if dist > settings.max_example_distance:
            continue
        bonus = 0.0
        if jurisdiction and m.get("jurisdiction") == jurisdiction:
            bonus += 0.03
        if section and m.get("section") == section:
            bonus += 0.03
        if m.get("doc_class") == doc_class:
            bonus += 0.02
        scored.append((dist - bonus, d, m["target"]))
    scored.sort(key=lambda x: x[0])
    return [(d, t) for _, d, t in scored[:k]]


# --------------------------------------------------------------------------
# Public API (unchanged signatures -- translator.py needs no edits)
# --------------------------------------------------------------------------
def find_terms(chunk, doc_class, src, tgt, jurisdiction=""):
    return termstore.lookup(chunk, doc_class, src, tgt, jurisdiction)


def find_examples(chunk, doc_class, src, tgt, jurisdiction="", section="", k=None):
    """Approved parallel passages most similar to this chunk. Same-jurisdiction and same-section
    matches get a small ranking bonus; weak matches are dropped (irrelevant examples hurt)."""
    if use_zilliz():
        return _find_examples_zilliz(chunk, doc_class, src, tgt, jurisdiction, section, k)
    return _find_examples_chroma(chunk, doc_class, src, tgt, jurisdiction, section, k)