"""End-to-end check of ingestion -> index -> retrieval on synthetic fixtures (no network, no LLM).
Run: python tests/test_pipeline.py"""
import os, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
os.environ.update(EMBED_BACKEND="hash", CHROMA_DIR=str(tmp / "chroma"), TERMS_DB=str(tmp / "terms.db"),
                  CORPUS_DIR=str(tmp / "corpus"), MAX_EXAMPLE_DISTANCE="0.6")
sys.path.insert(0, str(ROOT))
(tmp / "corpus" / "curated").mkdir(parents=True)
(tmp / "corpus" / "processed").mkdir(parents=True)
(tmp / "corpus" / "curated" / "seed_legal.csv").write_text((ROOT / "corpus/curated/seed_legal.csv").read_text(encoding="utf-8"), encoding="utf-8")

from adapters import formats, court, legislation  # noqa: E402
from adapters.common import Record, write_jsonl, good_pair  # noqa: E402
import build_index, rag, termstore  # noqa: E402

ok = 0
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)
    ok += 1

# --- TMX
(tmp / "t.tmx").write_text("""<?xml version="1.0"?><tmx version="1.4"><header/><body>
<tu><tuv xml:lang="ar-AE"><seg>حكمت المحكمة بإلزام المدعى عليه بسداد مبلغ 5000 درهم للمدعي.</seg></tuv>
<tuv xml:lang="en-US"><seg>The Court ordered the defendant to pay AED 5000 to the plaintiff.</seg></tuv></tu>
<tu><tuv xml:lang="ar"><seg>عدد خاطئ 7 في هذه الجملة الطويلة جدا</seg></tuv><tuv xml:lang="en"><seg>Wrong number 9 in this long enough sentence</seg></tuv></tu>
</body></tmx>""", encoding="utf-8")
pairs = [(s, t) for s, t in formats.read_tmx(tmp / "t.tmx", "ar", "en") if good_pair(s, t)]
check("tmx: reads pair and drops number-mismatched alignment", len(pairs) == 1)

# --- TBX
(tmp / "t.tbx").write_text("""<?xml version="1.0"?><martif><text><body>
<termEntry><langSet xml:lang="en"><tig><term>power of attorney</term></tig></langSet>
<langSet xml:lang="fr"><ntig><termGrp><term>procuration</term></termGrp></ntig></langSet></termEntry>
</body></text></martif>""", encoding="utf-8")
check("tbx: reads en/fr entry", list(formats.read_tbx(tmp / "t.tbx", "en", "fr")) == [("power of attorney", "procuration")])

# --- numbered court paragraphs (ECHR style incl. restarting numbers in operative part)
en = """THE FACTS
1. The applicant was born in 1970 and lives in Lyon.
2. On 5 May 2010 he lodged a complaint
with the prosecutor.
THE LAW
3. The Court notes that the complaint was lodged in time.
FOR THESE REASONS, THE COURT, UNANIMOUSLY,
1. Declares the application admissible;
2. Holds that there has been a violation."""
fr = """EN FAIT
1. Le requérant est né en 1970 et réside à Lyon.
2. Le 5 mai 2010, il a déposé une plainte
auprès du procureur.
EN DROIT
3. La Cour note que la plainte a été déposée à temps.
PAR CES MOTIFS, LA COUR, À L'UNANIMITÉ,
1. Déclare la requête recevable;
2. Dit qu'il y a eu violation."""
al = list(court.align_numbered(en, fr))
secs = {s[:20]: sec for s, t, sec in al}
check("numbered: aligned 5 paragraphs incl. operative items", len(al) == 5)
check("numbered: sections detected", {x[2] for x in al} == {"facts", "reasoning", "operative"})

# --- Justice Laws style XML
for lang, a, b in [("eng", "This Act may be cited as the Test Act.", "A person shall not act without authority granted under section 5."),
                   ("fra", "Titre abrégé : Loi d'essai.", "Nul ne peut agir sans l'autorisation accordée en vertu de l'article 5.")]:
    d = tmp / lang; d.mkdir()
    (d / "T-1.xml").write_text(f"""<Statute><Body><Section><Label>1</Label><MarginalNote>x</MarginalNote><Text>{a}</Text></Section>
<Section><Label>2</Label><Subsection><Label>(1)</Label><Text>{b}</Text></Subsection></Section></Body></Statute>""", encoding="utf-8")
jl = list(legislation.align_dirs(tmp / "eng", tmp / "fra"))
check("justice: section 1 and subsection 2/(1) both aligned by label", len(jl) == 2 and "authority" in jl[1][0] or "authority" in jl[0][0])

# --- TERMIUM-style CSV (zip), auto column detection
(tmp / "tm.csv").write_text("Domain,English term,French term\nLaw,power of attorney,procuration\nLaw,estoppel,préclusion\n", encoding="utf-8")
with zipfile.ZipFile(tmp / "tm.zip", "w") as z:
    z.write(tmp / "tm.csv", "domaine.csv")
check("termium: columns auto-detected", list(formats.read_termium(tmp / "tm.zip"))[0] == ("power of attorney", "procuration"))

# --- write processed JSONL and build index
recs = [Record("court_judgment", "ar", "en", "sentence", s, t, jurisdiction="AE", section="operative", origin="fixture", status="approved") for s, t in pairs]
recs += [Record("court_judgment", "en", "fr", "sentence", s, t, section=sec, origin="echr-fixture") for s, t, sec in al]
recs += [Record("common", "en", "fr", "term", s, t, jurisdiction="CA", origin="termium-fixture") for s, t in formats.read_termium(tmp / "tm.zip")]
recs += [Record("common", "ar", "fr", "sentence", "نص طويل بما يكفي للاختبار هنا", "texte assez long pour le test ici")]  # forbidden pair
write_jsonl(tmp / "corpus" / "processed" / "fixtures.jsonl", recs)
stats = build_index.main()
check("index: forbidden ar<->fr record rejected", stats.get("rejected") == 1)
check("index: terms and passages stored", termstore.count() > 150 and stats["passages"] > 10)

# --- term lookup: clitics, plurals, accents, longest match, jurisdiction, direction reversal
def terms(text, dc, s, t, j=""):
    return {a: b for a, b, _ in rag.find_terms(text, dc, s, t, j)}
r = terms("وبعد اطلاع المحكمة على صحيفة الدعوى، قدم الوكيل والموكل الطلب", "court_judgment", "ar", "en")
check("terms ar: clitic + definite article (صحيفة الدعوى)", r.get("صحيفة الدعوى") == "statement of claim")
r = terms("The principal appoints the agent. The parties agree on a formal notice.", "power_of_attorney", "en", "ar")
check("terms en->ar: reversed seed (principal -> الموكل)", r.get("principal") == "الموكل" and r.get("agent") == "الوكيل")
r = terms("Attendu que le demandeur a formé un pourvoi en cassation devant la Cour de cassation", "court_judgment", "fr", "en")
check("terms fr: longest match wins and accents/plurals fold", r.get("pourvoi en cassation") == "appeal on points of law (cassation)" and r.get("attendu que") == "whereas")
check("terms: word-boundary safe ('management' != 'agent')", "agent" not in terms("The management of the company", "power_of_attorney", "en", "ar"))
check("terms: Moroccan-only term excluded for AE", "عقد الازدياد" not in terms("عقد الازدياد", "civil_status", "ar", "en", "AE"))
check("terms: Moroccan-only term used for MA", terms("عقد الازدياد", "civil_status", "ar", "en", "MA").get("عقد الازدياد") == "birth certificate")
check("terms: class isolation (contract term not in civil_status)", "force majeure" not in terms("force majeure", "civil_status", "en", "fr"))
check("terms: TERMIUM 'common' term available in any class", terms("the estoppel doctrine", "contract", "en", "fr").get("estoppel") == "préclusion")

# --- passages
ex = rag.find_examples("حكمت المحكمة بإلزام المدعى عليه بسداد مبلغ 5000 درهم", "court_judgment", "ar", "en", "AE", "operative")
check("examples: retrieves approved AR->EN parallel passage", any("pay AED 5000" in t for _, t in ex))
ex = rag.find_examples("The applicant was born in 1970 and lives in Lyon.", "court_judgment", "en", "fr", "", "facts")
check("examples: EN->FR returns the French side of the ECHR-style fixture", any("Lyon" in t and "requérant" in t for _, t in ex))
check("examples: AR->EN passage not leaked into EN->FR results", not any("درهم" in d or "AED" in d for d, _ in ex))
ex = rag.find_examples("The Court ordered the defendant to pay AED 5000 to the plaintiff.", "contract", "en", "ar")
check("examples: class isolation (court passage not shown for a contract)", ex == [])
print(f"\n{ok} checks passed")
