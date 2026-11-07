import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

c = Counter()
for f in Path(settings.corpus_dir, "processed").glob("*.jsonl"):
    for line in open(f, encoding="utf-8"):
        r = json.loads(line)
        c[(r["doc_class"], f'{r["src_lang"]}->{r["tgt_lang"]}', r["kind"], r["origin"], r["status"])] += 1
print("doc_class | pair | kind | origin | status | count")
for k, v in sorted(c.items()):
    print(" | ".join(k), "|", v)
