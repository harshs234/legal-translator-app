import streamlit as st
from config import DOC_CLASSES, LANGS, ALLOWED_PAIRS, settings
from docreader import extract_text, detect_language
from classifier import classify
from translator import translate_document
from export import to_docx, clean_and_validate
import termstore

st.set_page_config(page_title="Legal Translator", layout="wide")
st.markdown("""<style>
.rtl{direction:rtl;text-align:right;font-size:1.05rem;line-height:1.9;white-space:pre-wrap}
.ltr{direction:ltr;text-align:left;white-space:pre-wrap}
</style>""", unsafe_allow_html=True)


def box(text, lang):
    cls = "rtl" if lang == "ar" else "ltr"
    safe = text.replace("&", "&amp;").replace("<", "&lt;")
    st.markdown(f'<div class="{cls}">{safe}</div>', unsafe_allow_html=True)


st.title("Legal Document Translator")
st.caption("Arabic ⇄ English and French ⇄ English. Classify → retrieve class-specific terminology and approved parallel passages → translate → QA flags → review.")

n_terms = termstore.count()
with st.sidebar:
    st.metric("Terminology entries", f"{n_terms:,}")
    st.caption(f"Record statuses in use: {', '.join(settings.allowed_status)}")
    if n_terms == 0:
        st.warning("The RAG stores are empty. Run `python build_index.py` first (see README).")

up = st.file_uploader("Upload PDF / DOCX / TXT", type=["pdf", "docx", "txt"])
pasted = st.text_area("…or paste text", height=120)

text = ""
if up:
    try:
        text = extract_text(up.name, up.getvalue())
    except Exception as e:
        st.error(str(e))
elif pasted.strip():
    text = pasted
if not text:
    st.stop()

if st.session_state.get("_text") != text:
    st.session_state.clear()
    st.session_state["_text"] = text
    st.session_state["src"] = detect_language(text)

c1, c2, c3 = st.columns(3)
sources = sorted({s for s, _ in ALLOWED_PAIRS})
src = c1.selectbox("Source language", sources, index=sources.index(st.session_state["src"]), format_func=LANGS.get)
tgt_options = sorted(t for s, t in ALLOWED_PAIRS if s == src)
tgt = c2.selectbox("Target language", tgt_options, format_func=LANGS.get)

if "cls" not in st.session_state:
    with st.spinner("Classifying document…"):
        try:
            st.session_state["cls"] = classify(text)
        except Exception as e:
            st.error(f"Classification failed: {e}")
            st.stop()
cls = st.session_state["cls"]

ids = list(DOC_CLASSES)
doc_class = c3.selectbox("Document type", ids, index=ids.index(cls["doc_class"]), format_func=lambda k: DOC_CLASSES[k][0])
jurisdiction = st.text_input("Jurisdiction (e.g. UAE, Saudi Arabia, Qatar, Canada). Selects the matching terminology.", value=cls["jurisdiction"])

if cls["confidence"] < settings.min_class_confidence:
    st.warning(f"Low classification confidence ({cls['confidence']:.0%}). Please confirm the document type. {cls['reason']}")
else:
    st.info(f"Detected **{DOC_CLASSES[cls['doc_class']][0]}** ({cls['confidence']:.0%}). {cls['reason']}")

if st.button("Translate", type="primary"):
    bar = st.progress(0.0)
    try:
        st.session_state["result"] = translate_document(text, src, tgt, doc_class, jurisdiction, progress=bar.progress)
        st.session_state["dirs"] = (src, tgt)
    except Exception as e:
        st.error(f"Translation failed: {e}")

res = st.session_state.get("result")
if res:
    s, t = st.session_state["dirs"]
    flagged = sum(1 for r in res if r["flags"])
    st.success(f"{len(res)} segments translated, {flagged} flagged for review.")

    # --- Pre-download validation gate -------------------------------------
    # Deliberately ABOVE the segment list: with 20+ segments the reviewer should
    # not have to scroll past the whole document to reach the download buttons.
    targets = [r["target"] for r in res]
    clean_paras, notes, issues = clean_and_validate(targets, target_is_arabic=(t == "ar"))
    blocking = [i["msg"] for i in issues if i["severity"] == "block"]
    advisories = [i["msg"] for i in issues if i["severity"] == "warn"]

    if blocking:
        st.error(f"{len(blocking)} blocking issue(s) must be reviewed before this draft is used:")
        for w in blocking:
            st.write(f"• {w}")
    if advisories:
        # Collapsed: these run to 20+ items on a real contract, and an expanded
        # list here would push the download buttons back down the page.
        with st.expander(f"⚠️ {len(advisories)} item(s) to verify"):
            for w in advisories:
                st.write(f"• {w}")
    if notes:
        with st.expander(f"Reviewer notes lifted out of the contract body ({len(notes)})"):
            for j, n in enumerate(notes, 1):
                st.write(f"{j}. {n}")

    full = "\n\n".join(clean_paras)
    can_download = True
    if blocking:
        can_download = st.checkbox(
            "I have reviewed the blocking issues above and want to download this draft anyway.",
            value=False,
        )
    if can_download:
        d1, d2 = st.columns(2)
        d1.download_button("Download .docx", to_docx(targets, rtl=(t == "ar")), file_name="translation.docx")
        d2.download_button("Download .txt", full.encode("utf-8"), file_name="translation.txt")
    else:
        st.info("Downloads are disabled until the blocking issues are acknowledged.")
    st.caption("Machine-assisted draft. Certified/sworn use requires review and signature by a qualified legal translator.")

    st.divider()

    # --- Side-by-side segments --------------------------------------------
    st.markdown("##### Segments")
    for i, r in enumerate(res, 1):
        a, b = st.columns(2)
        with a:
            box(r["source"], s)
        with b:
            box(r["target"], t)
        for f in r["flags"]:
            st.warning(f"Segment {i}: {f}")
        st.caption(f"Retrieval: {len(r['glossary'])} glossary terms, {r['n_examples']} reference passages"
                   + (f", section: {r['section']}" if r["section"] else ""))
        if r["glossary"]:
            with st.expander(f"Segment {i}: glossary applied"):
                for gs, gt, _ in r["glossary"]:
                    st.write(f"{gs} → {gt}")
        st.divider()