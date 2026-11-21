import re
from collections import Counter
from config import settings, ALLOWED_PAIRS
from jurisdiction import normalize_jurisdiction
from llm import complete_ex
from prompts import translate_system, translate_user
from rag import find_terms, find_examples

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_SENT = re.compile(r"(?<=[.!?؟؛;])\s+")

# PDF page-header/footer bleed, e.g. "Page 1 of 15" (Latin or Arabic-Indic digits).
# The source PDFs carry these on every page; left in, they get translated and pollute
# the output (and throw off segmentation). We drop whole-line occurrences up front.
_PAGE_LABEL = re.compile(
    r"(?im)^[ \t]*page[ \t]+[\d٠-٩۰-۹]+[ \t]+of[ \t]+[\d٠-٩۰-۹]+[ \t]*$"
)
# EUR-Lex / EU Official Journal running header that appears on every page with a
# different page number, e.g. "02016R0679 — FR — 04.05.2016 — 000.002 — 37".
# Each occurrence is unique (page number varies), so the frequency-based stripper
# can't catch it; we strip by pattern instead.
_EURLEX_HEADER = re.compile(
    r"(?im)^[ \t]*\d{5}[A-Z]\d{4}\s*[—–-]\s*[A-Z]{2}\s*[—–-]\s*\d{2}\.\d{2}\.\d{4}\s*[—–-].*$"
)

# A unit shorter than this is treated as a fragment (a lone list marker "a.", a clause
# number "9-25", or a stray word such as a negation "لا"/"No") and glued to its
# neighbour, so chunk packing can never orphan it at a boundary and split a negation
# from its verb. Kept small so short but complete clauses ("The Contract.") stand alone;
# anything longer that still slips through is caught by the export validation gate.
_FRAG_MIN = 8

# --- Output-budget settings (tuned for the Gemini free tier) --------------
# Gemini free-tier models return at most 8192 output tokens per response and
# then stop with finish_reason="length". We ask for that full ceiling, and we
# keep each chunk small enough that its translation lands well under it.
# Rule of thumb: ~1 token ≈ 3 chars, and Arabic→English expands ~1.3-1.6x, so a
# ~2800-char Arabic chunk yields ~1200-1500 output tokens — comfortably safe.
_MAX_OUTPUT_TOKENS = 8192
_SAFE_CHUNK_CHARS = 2800     # hard cap on chunk size regardless of config
_MIN_SPLIT_CHARS = 200       # don't recurse below this
_MAX_SPLIT_DEPTH = 4         # 2800 -> 1400 -> 700 -> 350 -> 175 worst case

_SECTIONS = [
    ("operative", re.compile(r"(for these reasons|par ces motifs|لهذه الأسباب|فلهذه الأسباب|حكمت المحكمة|قضت المحكمة)", re.I)),
    ("facts", re.compile(r"(^|\n)\s*(the facts|en fait|les faits|الوقائع)", re.I)),
    ("reasoning", re.compile(r"(^|\n)\s*(the law|en droit|reasons|motifs|أسباب الحكم|الأسباب)", re.I)),
]


def detect_section(chunk: str) -> str:
    head = chunk[:400]
    for name, pat in _SECTIONS:
        if pat.search(head):
            return name
    return ""


def _chunk_limit(limit=None) -> int:
    """Effective chunk size: honour config if set, but never exceed the safe cap."""
    if limit:
        return min(limit, _SAFE_CHUNK_CHARS)
    cfg = getattr(settings, "max_chunk_chars", None) or _SAFE_CHUNK_CHARS
    return min(cfg, _SAFE_CHUNK_CHARS)


def _strip_running_headers(text: str, min_repeat: int = 5, max_len: int = 60) -> str:
    """Drop running headers/footers and classification banners that repeat on nearly
    every page (e.g. "Entity Logo", "Form Name", "Page Number", "Restricted - مقيد").
    A short line that recurs many times across a document is page furniture, not clause
    text; left in it floods the translation and, when bilingual, trips the Arabic check.
    Only fires on genuinely repetitive boilerplate, so short documents are untouched."""
    lines = text.split("\n")
    norm = [ln.strip() for ln in lines]
    counts = Counter(n for n in norm if n)
    furniture = {n for n, c in counts.items() if c >= min_repeat and len(n) <= max_len}
    if not furniture:
        return text
    return "\n".join(ln for ln, n in zip(lines, norm) if n not in furniture)


def _merge_fragments(units: list[str]) -> list[str]:
    """Glue ultra-short units onto the following unit (or the previous one, if the
    fragment is last) so a chunk boundary can never separate an orphaned fragment —
    e.g. a lone negation "لا" / "No" left dangling away from the verb it governs.
    Merging forward is semantically safe: it only keeps neighbouring text together."""
    merged, carry = [], ""
    for u in units:
        if carry:
            u = f"{carry} {u}".strip()
            carry = ""
        if len(u) < _FRAG_MIN:
            carry = u
        else:
            merged.append(u)
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}".strip()
        else:
            merged.append(carry)
    return merged


def segment(text: str, limit: int = None) -> list[str]:
    """Paragraph-aware chunking; oversize paragraphs are split on sentence boundaries."""
    limit = _chunk_limit(limit)
    units = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= limit:
            units.append(para)
        else:
            units.extend(s for s in _SENT.split(para) if s.strip())
    units = _merge_fragments(units)
    chunks, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) + 1 > limit:
            chunks.append(cur)
            cur = u
        else:
            cur = f"{cur}\n{u}" if cur else u
    if cur:
        chunks.append(cur)
    return chunks


def _split_half(chunk: str) -> tuple[str, str]:
    """Split a chunk near its middle, preferring a line/sentence/word boundary."""
    mid = len(chunk) // 2
    for sep in ("\n", ". ", "؟ ", "؛ ", "; ", " "):
        left = chunk.rfind(sep, 0, mid)
        right = chunk.find(sep, mid)
        cands = [p for p in (left, right) if p != -1]
        if cands:
            pt = min(cands, key=lambda p: abs(p - mid)) + len(sep)
            a, b = chunk[:pt].strip(), chunk[pt:].strip()
            if a and b:
                return a, b
    return chunk[:mid].strip(), chunk[mid:].strip()


# Keywords that introduce a numbered "article/clause" unit — the granularity at which
# the model silently drops content — across the supported source AND output languages
# (ar/fr/en in, en/ar/fr out). COVERAGE is anchored on STANDALONE headings only (a
# heading alone on its own line, e.g. "Article 56" / "المادة ٥" / "Article (5)"), never
# on inline cross-references: a dropped "Article 56" is frequently still *mentioned*
# inside a retained neighbouring article ("…Article 56 shall not apply…"), so an
# "is 56 anywhere in the text" check would be masked by that reference and miss the drop.
_HEADING_KW = r"(?:articles?|clause|المادة|مادة|البند|بند)"
_HEADING_LINE = re.compile(
    rf"(?im)^[ \t]*{_HEADING_KW}[ \t]*\(?[ \t]*([\d٠-٩۰-۹]{{1,3}})[ \t]*\)?[ \t]*$"
)


def _heading_numbers(text: str) -> set:
    """Normalised numbers that appear as STANDALONE section headings in `text`."""
    return {m.group(1).translate(_DIGITS) for m in _HEADING_LINE.finditer(text)}


def _missing_headings(src_chunk: str, out: str) -> set:
    """Section headings present in the SOURCE chunk but absent as headings from the
    translated output — the fingerprint of an article the model dropped mid-chunk while
    still reporting a normal (non-truncated) finish. Defensive by construction: ANY
    failure, or a chunk with no headings at all, returns an empty set (= fully covered),
    so this check can never stop a document from translating. Its only effect is to ADD
    a re-translation attempt when content appears to have gone missing."""
    try:
        src_h = _heading_numbers(src_chunk)
        if not src_h:
            return set()
        return src_h - _heading_numbers(out)
    except Exception:
        return set()


def _numbers(s: str) -> Counter:
    return Counter(_NUM.findall(s.translate(_DIGITS)))


def qa_flags(src: str, out: str, glossary: list) -> list[str]:
    flags = []
    sn, on = _numbers(src), _numbers(out)
    if sn != on:
        # Show WHICH numbers differ so the reviewer can tell a harmless reformat
        # (date "01/05/2016" -> "1 May 2016", separators "500,000" <-> "500000")
        # from a real error ("500,000" -> "50,000") without hunting the segment.
        only_src = sorted((sn - on).elements())
        only_out = sorted((on - sn).elements())
        bits = []
        if only_src:
            bits.append("source only: " + ", ".join(only_src[:8]))
        if only_out:
            bits.append("translation only: " + ", ".join(only_out[:8]))
        flags.append("Numbers/dates differ between source and translation"
                     + ((" — " + "; ".join(bits)) if bits else ""))
    ratio = len(out) / max(len(src), 1)
    if ratio < 0.4 or ratio > 3.0:
        flags.append(f"Suspicious length ratio ({ratio:.2f}): possible omission or addition")
    if "[Translator's note" in out:
        flags.append("Translator's note: needs human review")
    missing = [t for _, t, _ in glossary if t.lower() not in out.lower()]
    if missing:
        flags.append("Glossary term not found in output: " + ", ".join(missing[:5]))
    return flags


def _translate_unit(system, chunk, src, tgt, doc_class, jur, section, prev_tail, depth=0):
    """Translate one chunk, then guarantee it was translated *in full*.

    Two ways a chunk can come back incomplete, both handled by splitting the chunk
    in half and translating each piece (recursively, down to a floor size):
      1. finish == "length"  — the model hit the output-token cap and was truncated.
      2. a coverage gap       — the model reported a normal finish but silently omitted
         a numbered article/clause mid-chunk (dense, repetitive legal text is prone to
         this). We catch it by comparing the section headings in the source chunk with
         those in the output; a heading that went missing means content was dropped.
    Halving reduces the per-call density that causes (2), so the dropped unit is
    almost always recovered on the smaller re-translation. Whatever cannot be recovered
    even at the floor size is flagged on the record (and caught again, document-wide, by
    the export numbering-gap check) rather than shipped silently.

    Returns (joined_translation, [segment_records]). A split chunk yields one record
    per sub-piece, which the UI simply renders as extra segments.
    """
    glossary = find_terms(chunk, doc_class, src, tgt, jur)
    examples = find_examples(chunk, doc_class, src, tgt, jur, section)
    out, finish = complete_ex(
        system,
        translate_user(chunk, glossary, examples, prev_tail),
        max_tokens=_MAX_OUTPUT_TOKENS,
    )

    dropped = _missing_headings(chunk, out)
    if (finish == "length" or dropped) and depth < _MAX_SPLIT_DEPTH and len(chunk) > _MIN_SPLIT_CHARS:
        left_src, right_src = _split_half(chunk)
        if left_src and right_src:
            lt, lrecs = _translate_unit(
                system, left_src, src, tgt, doc_class, jur, section, prev_tail, depth + 1)
            rt, rrecs = _translate_unit(
                system, right_src, src, tgt, doc_class, jur, section, lt[-500:], depth + 1)
            return lt + "\n" + rt, lrecs + rrecs

    rec = {"source": chunk, "target": out, "glossary": glossary, "section": section,
           "n_examples": len(examples), "flags": qa_flags(chunk, out, glossary)}
    if finish == "length":
        rec["flags"].append("Output hit the model length limit and may be truncated")
    if dropped:
        rec["flags"].append(
            "Possible dropped content: source heading(s) "
            + ", ".join(sorted(dropped, key=lambda n: int(n)))
            + " not found in the output and could not be recovered by re-splitting — "
              "verify this segment against the source")
    return out, [rec]


def translate_document(text, src, tgt, doc_class, jurisdiction="", progress=None):
    if (src, tgt) not in ALLOWED_PAIRS:
        raise ValueError(f"Unsupported direction {src}->{tgt}. Supported: ar<->en, fr<->en")
    text = _strip_running_headers(text)  # drop repeated page furniture / classification banners
    text = _PAGE_LABEL.sub("", text)  # drop "Page N of M" header bleed before chunking
    text = _EURLEX_HEADER.sub("", text)  # drop EUR-Lex/OJ running headers (unique per page, so pattern-stripped not frequency-stripped)
    jur = normalize_jurisdiction(jurisdiction)
    system = translate_system(src, tgt, doc_class, jurisdiction or "unknown")
    chunks = segment(text)
    results, prev_tail = [], ""
    for i, chunk in enumerate(chunks):
        section = detect_section(chunk) if doc_class == "court_judgment" else ""
        out, recs = _translate_unit(system, chunk, src, tgt, doc_class, jur, section, prev_tail)
        results.extend(recs)
        prev_tail = out[-500:]
        if progress:
            progress((i + 1) / len(chunks))
    return results