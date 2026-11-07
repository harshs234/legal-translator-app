"""Generic readers: TMX (translation memory), TBX (termbase, e.g. IATE), line-aligned files
(e.g. UN Parallel Corpus), and TERMIUM Plus CSV."""
import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from adapters.common import clean, lang2, good_pair

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def read_tmx(path, src, tgt):
    for _, el in ET.iterparse(path, events=("end",)):
        if _local(el.tag) != "tu":
            continue
        segs = {}
        for tuv in el:
            if _local(tuv.tag) != "tuv":
                continue
            lang = lang2(tuv.attrib.get(_XML_LANG) or tuv.attrib.get("lang", ""))
            for seg in tuv:
                if _local(seg.tag) == "seg":
                    segs.setdefault(lang, clean("".join(seg.itertext())))
        el.clear()
        if segs.get(src) and segs.get(tgt):
            yield segs[src], segs[tgt]


def read_tbx(path, src, tgt):
    for _, el in ET.iterparse(path, events=("end",)):
        if _local(el.tag) != "termEntry":
            continue
        by_lang = {}
        for ls in el.iter():
            if _local(ls.tag) == "langSet":
                lang = lang2(ls.attrib.get(_XML_LANG) or ls.attrib.get("lang", ""))
                for t in ls.iter():
                    if _local(t.tag) == "term" and (t.text or "").strip():
                        by_lang.setdefault(lang, clean(t.text))
        el.clear()
        if by_lang.get(src) and by_lang.get(tgt):
            yield by_lang[src], by_lang[tgt]


_LEGAL_HINTS = re.compile(
    r"\b(shall|pursuant|article|convention|treaty|court|tribunal|jurisdiction|agreement|obligation|"
    r"provisions?|law|legal|rights|statute|resolution|committee|party|parties)\b", re.I)


def read_lines(src_path, tgt_path, legal_only=False, min_hits=2):
    """Line-aligned plain-text pair (UN Parallel Corpus layout). legal_only keeps only lines whose
    English side has legal register markers. Pass the English file as whichever side is English."""
    with open(src_path, encoding="utf-8", errors="replace") as a, open(tgt_path, encoding="utf-8", errors="replace") as b:
        for s, t in zip(a, b):
            s, t = clean(s), clean(t)
            if not s or not t:
                continue
            if legal_only:
                en = s if re.search(r"[A-Za-z]{4}", s) and not re.search(r"[\u0600-\u06FF]", s) else t
                if len(_LEGAL_HINTS.findall(en)) < min_hits:
                    continue
            yield s, t


def _open_csv_text(path):
    if str(path).lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(name)
    else:
        raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def termium_headers(path):
    return next(csv.reader(io.StringIO(_open_csv_text(path))))


def read_termium(path, en_col=None, fr_col=None, max_words=8):
    """TERMIUM Plus open-data CSV. Column names are auto-detected; if detection fails run with
    --inspect to print the headers, then pass --en-col/--fr-col explicitly."""
    rows = csv.DictReader(io.StringIO(_open_csv_text(path)))
    heads = rows.fieldnames or []

    def find(pats):
        for h in heads:
            if any(re.search(p, h, re.I) for p in pats):
                return h
        return None

    en_col = en_col or find([r"\b(english|anglais)\b.*term", r"term.*\b(english|anglais)\b", r"^en(g)?(lish)?$", r"term.*_?en\b"])
    fr_col = fr_col or find([r"\b(french|fran[cç]ais)\b.*term", r"term.*\b(french|fran[cç]ais)\b", r"^fr(a)?(nch)?$", r"term.*_?fr\b"])
    if not en_col or not fr_col:
        raise SystemExit(f"Could not detect TERMIUM columns. Headers: {heads}\nRe-run with --en-col and --fr-col.")
    for r in rows:
        en, fr = clean(r.get(en_col, "")), clean(r.get(fr_col, ""))
        if en and fr and len(en.split()) <= max_words and len(fr.split()) <= max_words:
            yield en, fr
