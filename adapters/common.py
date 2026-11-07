import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Record:
    doc_class: str
    src_lang: str
    tgt_lang: str
    kind: str                # term | sentence (sentence = aligned sentence or paragraph)
    source: str
    target: str
    jurisdiction: str = ""   # ISO-style code: AE, SA, CA, UN, ...
    section: str = ""        # court docs: procedure | facts | reasoning | operative
    origin: str = ""         # where it came from (for audit and licence tracking)
    status: str = "official"  # official | approved | seed
    notes: str = ""


_WS = re.compile(r"\s+")
_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+")


def clean(s: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", s or "")).strip()


def lang2(code: str) -> str:
    return (code or "").split("-")[0].split("_")[0].lower()


def numbers(s: str) -> list:
    return sorted(_NUM.findall(s.translate(_DIG)))


def good_pair(src: str, tgt: str, lo: int = 15, hi: int = 1800) -> bool:
    """Alignment sanity filter: sane lengths, sane length ratio, identical numbers."""
    if not (lo <= len(src) <= hi and lo <= len(tgt) <= hi):
        return False
    if not 0.35 <= len(tgt) / max(len(src), 1) <= 3.2:
        return False
    return numbers(src) == numbers(tgt)


def write_jsonl(path, recs) -> int:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            n += 1
    return n
