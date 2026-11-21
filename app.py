import base64
import time
from pathlib import Path

import streamlit as st

from config import DOC_CLASSES, LANGS, ALLOWED_PAIRS, settings
from docreader import extract_text, detect_language
from classifier import classify
from translator import translate_document
from llm import reset_usage, read_usage
from export import to_docx, clean_and_validate
import termstore

# ============================================================================
# BRANDING — edit these three lines; drop your logo in assets/logo.png
# ============================================================================
APP_NAME = "Document Translator"                  # <-- product name shown in the header
COMPANY_NAME = "3i Infotech Ltd"          # <-- your company name
LOGO_PATH = Path("assets/logo.png")    # <-- replace this file with your logo
# Optional sidebar cost estimate. Set BOTH to your plan's USD price per
# 1,000,000 tokens to show an estimated cost; leave as None to show tokens only.
PRICE_IN_PER_1M = None
PRICE_OUT_PER_1M = None
# ============================================================================

st.set_page_config(page_title=f"{APP_NAME} — Legal Translation", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
:root{
  --paper:#FAF7F0; --card:#FFFFFF; --ink:#1C2B24;
  --green:#1E5244; --green-dk:#14362C; --amber:#C8762B; --amber-lt:#E3A869;
  --line:#E4DCCC; --soft:#F5F1E6; --muted:#7B8A82;
}
.block-container{padding-top:1.3rem;max-width:1200px}

/* ---------- hero ---------- */
.hero{position:relative;overflow:hidden;border-radius:4px;padding:28px 32px;margin-bottom:26px;
  background:linear-gradient(135deg,var(--green-dk) 0%,var(--green) 55%,var(--green-dk) 100%);
  border-left:6px solid var(--amber);box-shadow:0 16px 40px rgba(20,54,44,.26);
  display:flex;align-items:center;gap:20px}
.hero:after{content:"\\2696";position:absolute;right:38px;top:-22px;font-size:9rem;
  color:rgba(255,255,255,.055);pointer-events:none;line-height:1}
.hero img.lg{height:62px;border-radius:3px;background:var(--paper);padding:5px;
  box-shadow:0 6px 18px rgba(0,0,0,.3)}
.hero .ph{height:62px;width:62px;border-radius:3px;border:2px dashed rgba(255,255,255,.55);
  display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.75);font-size:.62rem;letter-spacing:.1em}
.hero .co{color:var(--amber-lt);font-size:.74rem;font-weight:700;letter-spacing:.22em;text-transform:uppercase}
.hero .ti{font-family:Georgia,'Times New Roman',serif;font-size:2.4rem;font-weight:700;
  color:#FDFBF6;line-height:1.08;letter-spacing:-.01em}
.hero .tg{color:#BFD3C9;font-size:.95rem;margin-top:4px;font-style:italic}

/* ---------- section heading + rule ---------- */
.sec{font-family:Georgia,serif;font-size:1.3rem;font-weight:700;color:var(--green-dk);margin:26px 0 0}
.rule{height:2px;background:linear-gradient(90deg,var(--amber),rgba(200,118,43,0));margin:10px 0 14px}

/* ---------- stat tiles ---------- */
.tiles{display:flex;gap:18px;margin-bottom:14px}
.tl{flex:1;background:var(--card);border:1px solid var(--line);border-top:3px solid var(--green);
  border-radius:3px;padding:16px 20px;box-shadow:0 2px 10px rgba(28,43,36,.05)}
.tl.w{border-top-color:var(--amber)}
.tl .n{font-family:Georgia,serif;font-size:1.9rem;font-weight:700;color:var(--green-dk);line-height:1}
.tl .l{font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-top:6px}

/* ---------- segment card ---------- */
.seg{background:var(--card);border:1px solid var(--line);border-radius:3px;overflow:hidden;
  margin-top:14px;box-shadow:0 2px 12px rgba(28,43,36,.06)}
.seg .body{display:flex}
.seg .pane{flex:1;padding:22px 26px}
.seg .pane.src{background:var(--soft);border-right:1px solid var(--line)}
.seg .hd{font-size:.68rem;letter-spacing:.18em;text-transform:uppercase;font-weight:700;
  color:#8A7A5E;margin-bottom:10px}
.seg .hd.t{color:var(--green)}
.seg .foot{padding:11px 26px;border-top:1px solid #EFE8D9;background:#FCFAF4;
  display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.chip{font-size:.76rem;padding:5px 12px;border-radius:3px;background:#FBEEDC;
  border:1px solid #E6C79B;color:#8A5618;font-weight:600}
.meta{font-size:.78rem;color:var(--muted);margin-left:auto}
.ar{direction:rtl;text-align:right;font-family:'Amiri','Noto Naskh Arabic',Georgia,serif;
  font-size:1.14rem;line-height:2.05;white-space:pre-wrap;color:#22332B}
.ltr{direction:ltr;text-align:left;font-size:1.0rem;line-height:1.78;white-space:pre-wrap;color:#22332B}

/* ---------- widgets ---------- */
.stButton button,.stDownloadButton button{border-radius:3px;font-weight:700;letter-spacing:.02em}
.side-brand{font-family:Georgia,serif;font-size:1.25rem;font-weight:700;color:var(--green-dk);
  border-bottom:2px solid var(--amber);padding-bottom:8px;margin-bottom:12px}

/* ---------- live progress panel ---------- */
.prog{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--amber);
  border-radius:3px;padding:18px 22px;margin:14px 0;box-shadow:0 2px 14px rgba(28,43,36,.08)}
.prog .top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px}
.prog .lab{font-family:Georgia,serif;font-size:1.18rem;font-weight:700;color:var(--green-dk)}
.prog .pct{font-family:Georgia,serif;font-size:2.1rem;font-weight:700;color:var(--amber);line-height:1}
.prog .track{height:13px;background:#EFE8D9;border-radius:999px;overflow:hidden;
  box-shadow:inset 0 1px 3px rgba(28,43,36,.12)}
.prog .fill{height:100%;border-radius:999px;background-color:var(--green);
  background-image:linear-gradient(135deg,rgba(255,255,255,.22) 25%,transparent 25%,transparent 50%,
    rgba(255,255,255,.22) 50%,rgba(255,255,255,.22) 75%,transparent 75%);
  background-size:26px 26px;animation:stripe 1s linear infinite;transition:width .35s ease}
@keyframes stripe{from{background-position:0 0}to{background-position:26px 0}}
.prog .meta{margin-top:11px;font-size:.85rem;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap}
.prog .dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--amber);
  margin-right:9px;animation:pulse 1.25s ease-in-out infinite;vertical-align:middle}
@keyframes pulse{0%,100%{opacity:.3;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
.prog.done{border-left-color:var(--green)}
.prog.done .pct{color:var(--green)}
.prog.done .fill{animation:none}
.side-sec{font-family:Georgia,serif;font-size:.82rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--green-dk);opacity:.75;margin:18px 0 8px}
.usage{background:var(--soft);border:1px solid var(--line);border-left:4px solid var(--amber);
  border-radius:4px;padding:12px 14px}
.usage .u-big{font-family:Georgia,serif;font-size:1.9rem;font-weight:700;color:var(--green-dk);line-height:1}
.usage .u-cap{font-size:.72rem;color:var(--muted);margin-bottom:9px}
.usage .u-split{display:flex;flex-direction:column;gap:3px;font-size:.8rem;color:var(--ink)}
.usage .u-split b{color:var(--green)}
.usage .u-cost{margin-top:8px;font-size:.8rem;color:var(--amber);font-weight:600}
.usage .u-sess{margin-top:8px;padding-top:8px;border-top:1px dashed var(--line);font-size:.75rem;color:var(--muted)}
.usage .u-sess b{color:var(--green-dk)}
.usage .u-live{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--amber);
  margin-right:8px;animation:pulse 1.25s ease-in-out infinite;vertical-align:middle}
.segno{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:var(--green);
  background:#EAF0EC;border:1px solid #CFE0D6;border-radius:3px;padding:4px 9px}
.rev{font-size:.9rem;color:var(--ink);margin:4px 0;line-height:1.5}
.rev b{color:var(--green-dk)}
</style>
""", unsafe_allow_html=True)


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _dur(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec} sec"
    m, r = divmod(sec, 60)
    if m < 60:
        return f"{m} min {r:02d} sec"
    h, m = divmod(m, 60)
    return f"{h} hr {m:02d} min"


def _usage_html(u, cum=None, live=False):
    def fmt(n):
        return f"{int(n or 0):,}"
    total = u.get("total", 0) or (u.get("prompt", 0) + u.get("completion", 0))
    dot = '<span class="u-live"></span>' if live else ''
    cap = "tokens &middot; so far" if live else "tokens &middot; last document"
    body = (
        f'<div class="u-big">{dot}{fmt(total)}</div>'
        f'<div class="u-cap">{cap}</div>'
        f'<div class="u-split">'
        f'<span>Input <b>{fmt(u.get("prompt", 0))}</b></span>'
        f'<span>Output <b>{fmt(u.get("completion", 0))}</b></span>'
        f'<span>Model calls <b>{fmt(u.get("calls", 0))}</b></span>'
        f'</div>'
    )
    if PRICE_IN_PER_1M is not None and PRICE_OUT_PER_1M is not None:
        c = (u.get("prompt", 0) / 1e6) * PRICE_IN_PER_1M + (u.get("completion", 0) / 1e6) * PRICE_OUT_PER_1M
        body += f'<div class="u-cost">est. cost ${c:,.4f}</div>'
    if cum and cum.get("total"):
        lab = "Session so far" if live else "Session total"
        body += (f'<div class="u-sess">{lab} <b>{fmt(cum["total"])}</b> tokens '
                 f'&middot; {fmt(cum.get("calls", 0))} calls</div>')
    return f'<div class="usage">{body}</div>'


def _render_token_slot(slot, live=False):
    """Paint the token-economy card into a placeholder. live=True reads the running
    accumulator (so the card climbs mid-translation); otherwise it shows the last
    completed document from session_state."""
    if live:
        u = read_usage()
        base = st.session_state.get("usage_cum") or {}
        cum = {k: base.get(k, 0) + u.get(k, 0)
               for k in ("prompt", "completion", "total", "calls")}
    else:
        u = st.session_state.get("usage") or {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
        cum = st.session_state.get("usage_cum")
    slot.markdown(_usage_html(u, cum, live=live), unsafe_allow_html=True)


def _progress_html(pct, done, total, elapsed, eta, finished=False):
    cls = "prog done" if finished else "prog"
    label = "Translation complete" if finished else "Translating document"
    dot = "" if finished else '<span class="dot"></span>'
    seg = (f"Segment {done} of {total}" if total else "Preparing…")
    right = (f"Finished in {_dur(elapsed)}" if finished
             else f"{_dur(elapsed)} elapsed &nbsp;·&nbsp; ~{_dur(eta)} remaining")
    return f'''
    <div class="{cls}">
      <div class="top"><span class="lab">{dot}{label}</span><span class="pct">{pct}%</span></div>
      <div class="track"><div class="fill" style="width:{pct}%"></div></div>
      <div class="meta"><span>{seg}</span><span>{right}</span></div>
    </div>'''


def _logo():
    if LOGO_PATH.exists():
        b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
        return f'<img class="lg" src="data:image/png;base64,{b64}"/>'
    return '<div class="ph">LOGO</div>'


def section(title):
    st.markdown(f'<div class="sec">{title}</div><div class="rule"></div>', unsafe_allow_html=True)


st.markdown(f"""
<div class="hero">{_logo()}
  <div>
    <div class="co">{COMPANY_NAME}</div>
    <div class="ti">{APP_NAME}</div>
    <div class="tg">Terminology-aware legal translation — Arabic ⇄ English · French ⇄ English</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ---- sidebar ---------------------------------------------------------------
n_terms = termstore.count()
token_slot = None  # placeholder the translate loop updates live (set below)
with st.sidebar:
    st.markdown(f'<div class="side-brand">{COMPANY_NAME}</div>', unsafe_allow_html=True)
    st.metric("Terminology entries", f"{n_terms:,}")
    st.caption(f"Record statuses in use: {', '.join(settings.allowed_status)}")
    if n_terms == 0:
        st.warning("The RAG stores are empty. Run `python build_index.py` first (see README).")
    with st.expander("How it works"):
        st.markdown(
            "1. **Classify** the document type\n"
            "2. **Retrieve** matching terminology + parallel passages\n"
            "3. **Translate** consistently across the document\n"
            "4. **Verify** — flag numbers, omissions and terms for review"
        )
    _translating = st.session_state.get("translating")
    _u = st.session_state.get("usage")
    if _translating or _u:
        st.markdown('<div class="side-sec">Token economy</div>', unsafe_allow_html=True)
        token_slot = st.empty()
        _render_token_slot(token_slot, live=bool(_translating))
    st.caption("Machine-assisted draft. Not a substitute for a certified legal translator.")

# ---- 1 · input -------------------------------------------------------------
section("Upload a document")
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

# ---- 2 · settings ----------------------------------------------------------
section("Confirm settings")
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

# ---- translate (button flips to "Translating…" while it runs) --------------
if st.session_state.get("translating"):
    st.button("Translating\u2026", type="primary", disabled=True, use_container_width=True)
    panel = st.empty()
    panel.markdown(_progress_html(0, 0, 0, 0, 0), unsafe_allow_html=True)

    state = {"n": None, "t0": time.time()}

    def on_progress(frac):
        frac = min(max(float(frac), 0.0), 1.0)
        if state["n"] is None and frac > 0:
            state["n"] = max(1, round(1 / frac))
        total = state["n"] or 0
        done = max(1, round(frac * total)) if total else 0
        elapsed = time.time() - state["t0"]
        eta = (elapsed / done) * (total - done) if done and total else 0
        panel.markdown(
            _progress_html(int(frac * 100), done, total, elapsed, eta),
            unsafe_allow_html=True,
        )
        if token_slot is not None:
            _render_token_slot(token_slot, live=True)

    try:
        reset_usage()
        st.session_state["result"] = translate_document(
            text, src, tgt, doc_class, jurisdiction, progress=on_progress
        )
        st.session_state["dirs"] = (src, tgt)
        usage = read_usage()
        st.session_state["usage"] = usage
        cum = st.session_state.get(
            "usage_cum", {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
        for k in ("prompt", "completion", "total", "calls"):
            cum[k] = cum.get(k, 0) + usage.get(k, 0)
        st.session_state["usage_cum"] = cum
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        st.session_state["translating"] = False
        panel.empty()
        st.error(f"Translation failed: {e}")
        st.stop()

    st.session_state["translating"] = False
    st.rerun()
else:
    if st.button("Translate document", type="primary", use_container_width=True):
        reset_usage()  # zero the counter now so the live card starts at 0 on rerun
        st.session_state["translating"] = True
        st.rerun()

# ---- 3 · results -----------------------------------------------------------
res = st.session_state.get("result")
if res:
    s, t = st.session_state["dirs"]
    flagged = sum(1 for r in res if r["flags"])
    section("Review &amp; export")

    st.markdown(f"""
    <div class="tiles">
      <div class="tl"><div class="n">{len(res)}</div><div class="l">Segments</div></div>
      <div class="tl w"><div class="n">{flagged}</div><div class="l">Flagged for review</div></div>
      <div class="tl"><div class="n">{s.upper()} &rarr; {t.upper()}</div><div class="l">Direction</div></div>
    </div>
    """, unsafe_allow_html=True)

    targets = [r["target"] for r in res]
    clean_paras, notes, issues = clean_and_validate(targets, target_is_arabic=(t == "ar"))
    blocking = [i for i in issues if i["severity"] == "block"]
    advisories = [i for i in issues if i["severity"] == "warn"]

    def _loc(seg):
        # Segments are 1-indexed in the "Segment by segment" view below.
        return f"Segment {seg + 1}" if seg is not None else "Document-wide"

    if blocking:
        st.error(f"{len(blocking)} blocking issue(s) must be reviewed before this draft is used:")
        for it in blocking:
            st.markdown(f'<div class="rev">• <b>{_loc(it["seg"])}</b> — {_esc(it["msg"])}</div>',
                        unsafe_allow_html=True)
    if advisories:
        with st.expander(f"⚠️ {len(advisories)} item(s) to verify"):
            for it in advisories:
                st.markdown(f'<div class="rev">• <b>{_loc(it["seg"])}</b> — {_esc(it["msg"])}</div>',
                            unsafe_allow_html=True)
    if notes:
        with st.expander(f"Reviewer notes lifted out of the document body ({len(notes)})"):
            for j, n in enumerate(notes, 1):
                seg = n.get("seg") if isinstance(n, dict) else None
                text = n["text"] if isinstance(n, dict) else n
                st.markdown(f'<div class="rev">{j}. <b>{_loc(seg)}</b> — {_esc(text)}</div>',
                            unsafe_allow_html=True)

    full = "\n\n".join(clean_paras)
    can_download = True
    if blocking:
        can_download = st.checkbox(
            "I have reviewed the blocking issues above and want to download this draft anyway.",
            value=False,
        )
    if can_download:
        d1, d2 = st.columns(2)
        d1.download_button("Download Word (.docx)", to_docx(targets, rtl=(t == "ar")),
                           file_name=f"{APP_NAME.lower()}_translation.docx",
                           type="primary", use_container_width=True)
        d2.download_button("Plain text (.txt)", full.encode("utf-8"),
                           file_name=f"{APP_NAME.lower()}_translation.txt",
                           use_container_width=True)
    else:
        st.info("Downloads are disabled until the blocking issues are acknowledged.")
    st.caption("Machine-assisted draft. Certified/sworn use requires review and signature by a qualified legal translator.")

    # ---- segments ----
    section("Segment by segment")
    s_cls = "ar" if s == "ar" else "ltr"
    t_cls = "ar" if t == "ar" else "ltr"
    s_lab, t_lab = LANGS.get(s, s), LANGS.get(t, t)

    for i, r in enumerate(res, 1):
        chips = "".join(f'<span class="chip">&#9873; {_esc(f)}</span>' for f in r["flags"])
        meta = (f'Retrieval: {len(r["glossary"])} glossary terms, '
                f'{r["n_examples"]} reference passages'
                + (f', section: {_esc(str(r["section"]))}' if r["section"] else ""))
        st.markdown(f"""
        <div class="seg">
          <div class="body">
            <div class="pane src">
              <div class="hd">Source &middot; {s_lab}</div>
              <div class="{s_cls}">{_esc(r["source"])}</div>
            </div>
            <div class="pane">
              <div class="hd t">Translation &middot; {t_lab}</div>
              <div class="{t_cls}">{_esc(r["target"])}</div>
            </div>
          </div>
          <div class="foot"><span class="segno">Segment {i}</span>{chips}<span class="meta">{meta}</span></div>
        </div>
        """, unsafe_allow_html=True)
        if r["glossary"]:
            with st.expander(f"Segment {i} — glossary applied ({len(r['glossary'])})"):
                for gs, gt, _ in r["glossary"]:
                    st.write(f"{gs} → {gt}")