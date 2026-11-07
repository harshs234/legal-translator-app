"""
Is the Zilliz collection actually FINISHED indexing and loading?

AUTOINDEX builds asynchronously after a bulk load. A search issued against a
partially-built index silently returns incomplete results -- some segments get
searched, others don't -- which looks exactly like random recall loss on a
minority of queries while most queries look perfect.

Run this until everything reads READY, then re-run diagnose_overlap.py.

    python check_index_status.py          # one check
    python check_index_status.py --watch  # poll every 30s until ready
"""
import os
import sys
import time

if not (os.getenv("ZILLIZ_URI") and os.getenv("ZILLIZ_TOKEN")):
    sys.exit("Set ZILLIZ_URI and ZILLIZ_TOKEN first.")

from pymilvus import MilvusClient

URI = os.getenv("ZILLIZ_URI").strip()
TOKEN = os.getenv("ZILLIZ_TOKEN").strip()
COLL = os.getenv("ZILLIZ_COLLECTION", "legal_tm")
EXPECTED = int(os.getenv("EXPECTED_ROWS", "562310"))

client = MilvusClient(uri=URI, token=TOKEN)


def report():
    ready = True
    print("=" * 60)
    print(f"collection: {COLL}")
    print("=" * 60)

    # --- rows ---
    try:
        rows = client.get_collection_stats(COLL)["row_count"]
        ok = rows >= EXPECTED
        ready &= ok
        print(f"  rows            : {rows:,} / {EXPECTED:,}   {'OK' if ok else 'INCOMPLETE'}")
    except Exception as e:
        ready = False
        print(f"  rows            : error -> {e}")

    # --- load state ---
    try:
        st = client.get_load_state(COLL)
        val = st.get("state", st) if isinstance(st, dict) else st
        s = str(val)
        ok = "Loaded" in s or "LOADED" in s.upper()
        ready &= ok
        print(f"  load state      : {s}   {'OK' if ok else 'NOT LOADED'}")
    except Exception as e:
        print(f"  load state      : unavailable ({e})")

    # --- index build progress ---
    try:
        names = client.list_indexes(COLL)
        for n in names:
            d = client.describe_index(COLL, n)
            state = str(d.get("state", "?"))
            indexed = d.get("indexed_rows")
            pending = d.get("pending_index_rows")
            total = d.get("total_rows")
            ok = ("Finished" in state) and (not pending or int(pending) == 0)
            ready &= ok
            print(f"  index '{n}'")
            print(f"      state       : {state}   {'OK' if ok else 'BUILDING'}")
            if indexed is not None:
                print(f"      indexed rows: {indexed}")
            if pending is not None:
                print(f"      pending rows: {pending}")
            if total is not None:
                print(f"      total rows  : {total}")
            if d.get("index_type"):
                print(f"      type/metric : {d.get('index_type')} / {d.get('metric_type')}")
    except Exception as e:
        print(f"  index           : unavailable ({e})")

    print()
    print("  >>> READY -- re-run diagnose_overlap.py" if ready
          else "  >>> NOT READY -- wait and check again")
    return ready


if "--watch" in sys.argv:
    while True:
        if report():
            break
        print("\n  sleeping 30s ...\n")
        time.sleep(30)
else:
    report()