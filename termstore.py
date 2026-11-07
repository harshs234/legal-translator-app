"""Exact terminology lookup (SQLite). Scales to millions of terms (e.g. TERMIUM/IATE) because
a chunk is matched by hashing its n-grams, not by scanning the glossary."""
import re
import sqlite3
import unicodedata
from config import settings

MAX_TOK = 8
_TASHKEEL = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_AR = re.compile(r"[\u0600-\u06FF]")
_AR_PREFIX = re.compile(r"^(?:وال|بال|كال|فال|لل|ال)")
_TOKEN = re.compile(r"\w+", re.UNICODE)
_STATUS_RANK = {"approved": 0, "official": 1, "seed": 2}


def canon(tok: str) -> str:
    tok = unicodedata.normalize("NFKC", tok).lower()
    if _AR.search(tok):
        tok = re.sub("[\u0622\u0623\u0625]", "\u0627", tok).replace("\u0649", "\u064A").replace("\u0629", "\u0647")
        m = _AR_PREFIX.match(tok)
        if m and len(tok) - m.end() >= 3:
            tok = tok[m.end():]
        return tok
    tok = "".join(c for c in unicodedata.normalize("NFD", tok) if unicodedata.category(c) != "Mn")
    if len(tok) > 3 and tok[-1] in "sx":
        tok = tok[:-1]
    return tok


def tokens(text: str) -> list[str]:
    return [canon(t) for t in _TOKEN.findall(_TASHKEEL.sub("", text))]


def key_of(term: str) -> str:
    return " ".join(tokens(term))


def _con():
    con = sqlite3.connect(settings.terms_db)
    con.execute("""CREATE TABLE IF NOT EXISTS terms(
        key TEXT, src_lang TEXT, tgt_lang TEXT, doc_class TEXT, source TEXT, target TEXT,
        jurisdiction TEXT, status TEXT, origin TEXT, notes TEXT, ntok INTEGER,
        UNIQUE(key, src_lang, tgt_lang, doc_class, target, jurisdiction))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_terms ON terms(src_lang, tgt_lang, key)")
    return con


def reset():
    con = _con()
    con.execute("DELETE FROM terms")
    con.commit()
    con.close()


def add_terms(recs) -> int:
    """recs: iterable of Record-like objects (kind == term). Returns rows inserted."""
    rows = []
    for r in recs:
        key = key_of(r.source)
        n = len(key.split())
        if n == 0 or n > MAX_TOK or (n == 1 and len(key) < 3):
            continue
        rows.append((key, r.src_lang, r.tgt_lang, r.doc_class, r.source, r.target,
                     r.jurisdiction, r.status, r.origin, r.notes, n))
    con = _con()
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO terms VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    added = con.total_changes - before
    con.close()
    return added


def count() -> int:
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM terms").fetchone()[0]
    con.close()
    return n


def lookup(chunk, doc_class, src, tgt, jurisdiction=""):
    """Returns [(source_term, target_term, notes)] in order of appearance, longest match first."""
    toks = tokens(chunk)
    if not toks:
        return []
    con = _con()
    try:
        maxn = min(con.execute("SELECT MAX(ntok) FROM terms WHERE src_lang=? AND tgt_lang=?", (src, tgt)).fetchone()[0] or 0, MAX_TOK)
        if maxn == 0:
            return []
        spans = {}
        for n in range(1, maxn + 1):
            for i in range(len(toks) - n + 1):
                spans.setdefault(" ".join(toks[i:i + n]), []).append((i, i + n))
        keys, st, rows = list(spans), settings.allowed_status, []
        for j in range(0, len(keys), 400):
            part = keys[j:j + 400]
            q = ("SELECT key, doc_class, source, target, jurisdiction, status, notes FROM terms "
                 "WHERE src_lang=? AND tgt_lang=? AND doc_class IN (?,?) "
                 f"AND status IN ({','.join('?' * len(st))}) AND key IN ({','.join('?' * len(part))})")
            rows += con.execute(q, (src, tgt, doc_class, "common", *st, *part)).fetchall()
    finally:
        con.close()

    best = {}
    for key, dc, s, t, jur, status, notes in rows:
        if jur and jurisdiction and jur != jurisdiction:
            continue  # jurisdiction-specific term, wrong jurisdiction
        rank = (0 if dc == doc_class else 1,
                0 if (jur and jur == jurisdiction) else (1 if not jur else 2),
                _STATUS_RANK.get(status, 3))
        if key not in best or rank < best[key][0]:
            best[key] = (rank, s, t, notes)

    covered, hits = set(), []
    for key in sorted(best, key=lambda k: -len(k.split())):
        for a, b in spans[key]:
            if any(x in covered for x in range(a, b)):
                continue
            covered.update(range(a, b))
            hits.append((a, best[key][1], best[key][2], best[key][3]))
            break
    hits.sort()
    return [(s, t, n) for _, s, t, n in hits]
