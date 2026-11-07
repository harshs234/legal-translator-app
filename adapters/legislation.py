"""Canada Justice Laws bulk XML (consolidated Acts/regulations), English <-> French.
Sections/subsections are aligned by their labels. NOTE: written against the documented element
names (Section/Subsection/Label/Text/MarginalNote); always run with --dry-run first on real files."""
import xml.etree.ElementTree as ET
from pathlib import Path
from adapters.common import clean, good_pair


def _local(tag):
    return tag.split("}")[-1]


def _flat(el):
    return clean("".join(el.itertext()))


def sections(path) -> dict:
    out = {}
    root = ET.parse(path).getroot()
    for sec in root.iter():
        if _local(sec.tag) != "Section":
            continue
        label = next((_flat(c) for c in sec if _local(c.tag) == "Label"), "")
        if not label:
            continue
        subs = [c for c in sec if _local(c.tag) == "Subsection"]
        if subs:
            for sub in subs:
                sl = next((_flat(c) for c in sub if _local(c.tag) == "Label"), "")
                if sl:
                    out.setdefault(f"{label}/{sl}", _flat(sub))
        else:
            text = next((c for c in sec if _local(c.tag) == "Text"), None)
            out.setdefault(label, _flat(text) if text is not None else _flat(sec))
    return out


def align_dirs(eng_dir, fra_dir):
    eng = {p.stem: p for p in Path(eng_dir).rglob("*.xml")}
    fra = {p.stem: p for p in Path(fra_dir).rglob("*.xml")}
    matched = sorted(set(eng) & set(fra))
    print(f"[justice] files: eng={len(eng)} fra={len(fra)} matched={len(matched)}")
    for stem in matched:
        en, fr = sections(eng[stem]), sections(fra[stem])
        for key in sorted(set(en) & set(fr)):
            if good_pair(en[key], fr[key], hi=3000):
                yield en[key], fr[key]
