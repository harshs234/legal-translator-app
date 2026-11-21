"""DOCX export with a cleanup + validation gate.

The translator should never emit markup, but we treat export as the last line
of defence: strip any glossary tags / placeholder brackets that leaked through,
lift [Translator's note: ...] out of the contract body into a separate reviewer
section, and report anything suspicious (stray Arabic in an English target,
leftover markup, obvious unfinished sentences) so the UI can warn before the
user downloads. Root-cause fixes still belong in prompts.py — this is a net.
"""
import io
import re
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH

# <glossary>term</glossary>  -> term
_GLOSSARY_TAG = re.compile(r"</?glossary>", re.I)
# <compensation>, <term of the contract>, <confidentiality> -> inner text
# (lowercase start, letters/digits/space/-/ only, so it can't match emails/XML)
_ANGLE_TERM = re.compile(r"<([a-z][a-z0-9 /\-]{1,48})>")
# [Translator's note: ...]  (may span lines)
_NOTE = re.compile(r"\[translator'?s note:\s*(.*?)\]", re.I | re.S)
_ARABIC = re.compile(r"[؀-ۿ]")
# a line that ends mid-thought with a dangling preposition/article — a reliable
# truncation signal. Deliberately EXCLUDES "and"/"or": legal sub-clauses routinely end
# "; and/or" or "; or" as list enumeration, which is correct drafting, not truncation —
# flagging those just trains the reviewer to ignore the panel. Split negations that DO
# change meaning are caught as blocking issues by _ORPHAN / _ORPHAN_NEG instead.
_DANGLING = re.compile(r"\b(of|to|the|for|with|in|that|which|by|from)\s*$", re.I)
# a whole line that is nothing but a stray connective/negation — the tell-tale of a
# sentence split across a segment boundary (e.g. an orphaned "No" / "لا"). No trailing
# period, so a lone "No." (the Number column header in a specs table) is never flagged.
_ORPHAN = re.compile(r"^\s*(no|not|and|or|the|nor|but|لا|و|أو|لم|لن)\s*$", re.I)
# a line that TRAILS off with a dangling bare negation after a sentence boundary —
# the "…labor disputes. No" artifact. Requires no trailing period, so the "No." /
# "Number" abbreviation ("Contract No.", "bearing No.") is never matched.
_ORPHAN_NEG = re.compile(r"(?:[.!?؟؛:]\s+|^\s*)(no|not|nor|neither|لا|لم|لن|ليس|غير)\s*$", re.I)
# PDF page-header bleed that may still ride through ("Page 1 of 15", Latin/Arabic digits).
_PAGE_LABEL = re.compile(
    r"(?i)^\s*page\s+[\d٠-٩۰-۹]+\s+of\s+[\d٠-٩۰-۹]+\s*$"
)
# Cross-references to annexes: surfaced for the reviewer to verify against the real
# annex set (catches a hallucinated reference such as "Schedule (10)").
_SCHED_REF = re.compile(r"\b(schedule|appendix|annex)\s*\(?\s*(\d{1,3})\s*\)?", re.I)
# A STANDALONE article heading on its own line: "Article 56", "Article (56)". Anchored
# to the whole line (^...$) so an inline cross-reference ("pursuant to Article 60") is
# never matched — only genuine section headings count toward the numbering sequence.
_ARTICLE_HEAD = re.compile(r"^\s*article\s*\(?\s*(\d{1,3})\s*\)?\s*$", re.I)


def _numbering_gaps(joined: str) -> list[str]:
    """Detect dropped sections in sequentially-numbered legal text (legislation,
    regulations, and most civil-law contracts). The LLM can silently omit a dense,
    repetitive article mid-chunk and still report finish_reason="stop", so the
    length-recovery never fires — the only trace is a hole in the article sequence
    (e.g. Article 55 followed directly by Article 58). We surface that hole for the
    reviewer. Advisory only: numbering that is intentionally non-sequential (some
    contracts) at worst produces a note to verify, never a blocked download."""
    nums = [int(m.group(1)) for line in joined.split("\n")
            if (m := _ARTICLE_HEAD.match(line.strip()))]
    if len(nums) < 5:  # too few headings to trust that this is sequential numbering
        return []
    msgs = []
    for a, b in zip(nums, nums[1:]):
        gap = b - a
        if 1 < gap <= 10:  # a plausible drop, not a section/chapter renumbering reset
            missing = ", ".join(str(n) for n in range(a + 1, b))
            msgs.append(f"Possible dropped content: article numbering jumps from {a} "
                        f"to {b} — Article(s) {missing} not found as a heading. Check "
                        f"the source to confirm nothing was omitted in translation.")
    return msgs


def _clean_line(line: str, notes: list, seg: int = None) -> str:
    line = _GLOSSARY_TAG.sub("", line)
    line = _ANGLE_TERM.sub(r"\1", line)

    def _grab(m):
        notes.append({"text": m.group(1).strip(), "seg": seg})
        return f"[see reviewer note {len(notes)}]"

    line = _NOTE.sub(_grab, line)
    # collapse the double spaces left behind by removals
    return re.sub(r"[ \t]{2,}", " ", line).strip()


def clean_and_validate(paragraphs: list[str], target_is_arabic: bool):
    """Return (clean_paragraphs, notes, issues) without touching a file.

    issues is a list of {"severity": "block"|"warn", "msg": str}. A "block" issue is
    one that must not pass silently into a final document (untranslated text, an
    orphaned fragment, surviving markup); a "warn" issue is advisory (verify this).
    The UI blocks the download on any "block" issue unless the user overrides.
    """
    clean_paras, notes, issues = [], [], []

    def add(sev: str, msg: str, seg: int = None):
        issues.append({"severity": sev, "msg": msg, "seg": seg})

    for p_idx, p in enumerate(paragraphs):
        out_lines = []
        for line in p.split("\n"):
            if _PAGE_LABEL.match(line):
                continue  # drop page-header bleed that slipped past preprocessing
            c = _clean_line(line, notes, seg=p_idx)
            if not c:
                continue
            out_lines.append(c)
            # English target should contain no Arabic script -> blocks download
            if not target_is_arabic and _ARABIC.search(c):
                add("block", f"Untranslated Arabic left in the English output: “{c[:60]}…”", seg=p_idx)
            # a whole line that is just a connective/negation, or a line trailing off
            # with a dangling bare negation -> sentence split across a boundary
            if _ORPHAN.match(c) or _ORPHAN_NEG.search(c):
                add("block", f"Orphaned sentence fragment (likely a split error): “…{c[-40:].strip()}”", seg=p_idx)
            elif _DANGLING.search(c) and len(c) > 40:
                add("warn", f"Possible unfinished sentence: “…{c[-60:]}”", seg=p_idx)
        clean_paras.append("\n".join(out_lines))

    joined = "\n".join(clean_paras)
    # Markup should be gone now; if any survived, block on it.
    if "<glossary" in joined.lower() or "</glossary" in joined.lower():
        add("block", "Glossary markup still present after cleanup.")
    # Surface every annex cross-reference for the reviewer to verify (catches a
    # hallucinated reference like "Schedule (10)" when the doc has only 1–5).
    refs = sorted({f"{m.group(1).title()} ({m.group(2)})" for m in _SCHED_REF.finditer(joined)})
    if refs:
        add("warn", "Verify these annex cross-references against the document's actual "
                    "schedules/appendices: " + ", ".join(refs))
    # Flag holes in the article sequence (a dropped section the model omitted silently).
    for msg in _numbering_gaps(joined):
        add("warn", msg)

    # de-duplicate by message while preserving order
    seen, uniq = set(), []
    for it in issues:
        if it["msg"] not in seen:
            seen.add(it["msg"])
            uniq.append(it)
    return clean_paras, notes, uniq


def to_docx(paragraphs: list[str], rtl: bool) -> bytes:
    clean_paras, notes, _issues = clean_and_validate(paragraphs, target_is_arabic=rtl)

    doc = Document()
    for p in clean_paras:
        for line in p.split("\n"):
            para = doc.add_paragraph(line)
            if rtl:
                pPr = para._p.get_or_add_pPr()
                pPr.append(OxmlElement("w:bidi"))
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                for run in para.runs:
                    rPr = run._r.get_or_add_rPr()
                    rPr.append(OxmlElement("w:rtl"))

    # Reviewer notes live in their own section, never inside the contract body.
    if notes:
        doc.add_page_break()
        doc.add_heading("Reviewer Notes — not part of the translated text", level=1)
        for i, n in enumerate(notes, 1):
            seg = n.get("seg") if isinstance(n, dict) else None
            text = n["text"] if isinstance(n, dict) else n
            loc = f" (Segment {seg + 1})" if seg is not None else ""
            doc.add_paragraph(f"{i}. {text}{loc}", style="List Number")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()