"""Court judgments with numbered paragraphs (ECHR/HUDOC, Supreme Court of Canada, many tribunals).
Paragraph numbers are identical across language versions, so alignment is exact by number.
Section labels (facts/reasoning/operative) come from the headings."""
import re
from adapters.common import clean, good_pair

_HEADINGS = [
    ("operative", r"(FOR THESE REASONS|PAR CES MOTIFS|لهذه الأسباب)"),
    ("procedure", r"(PROCEDURE|PROCÉDURE|PROCEDURE)"),
    ("facts", r"(THE FACTS|EN FAIT|FACTS|LES FAITS|CIRCUMSTANCES OF THE CASE)"),
    ("reasoning", r"(THE LAW|EN DROIT|ANALYSIS|ANALYSE|REASONS|MOTIFS|DISCUSSION|ASSESSMENT)"),
]
_HEAD_RE = [(n, re.compile(r"^(?:[IVX]+\.\s*)?" + p + r"\b")) for n, p in _HEADINGS]
_NUM_RE = [re.compile(r"^\s*(\d{1,4})\.\s+(\S.*)$"), re.compile(r"^\s*\[(\d{1,4})\]\s+(\S.*)$")]


def parse_numbered(text: str) -> dict:
    paras, cur, section = {}, None, ""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if len(s) <= 80:
            hit = next((n for n, rx in _HEAD_RE if rx.match(s.upper())), None)
            if hit:
                section, cur = hit, None
                continue
        m = next((rx.match(line) for rx in _NUM_RE if rx.match(line)), None)
        if m:
            key = (int(m.group(1)), "op" if section == "operative" else "body")
            if key in paras:
                cur = None  # duplicate number: ignore, avoid corrupting alignment
            else:
                paras[key] = [m.group(2), section]
                cur = key
        elif cur:
            paras[cur][0] += " " + s
    return paras


def align_numbered(src_text, tgt_text):
    a, b = parse_numbered(src_text), parse_numbered(tgt_text)
    for key in sorted(set(a) & set(b)):
        s, t = clean(a[key][0]), clean(b[key][0])
        if good_pair(s, t, hi=3000):
            yield s, t, (a[key][1] or b[key][1])
