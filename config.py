import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _csv(name, default):
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    # Optional explicit key/endpoint. If api_key is blank, LiteLLM falls back to the
    # provider's own env var (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...).
    api_key: str = (os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
                    or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY") or "")
    api_base: str = os.getenv("LLM_API_BASE", "")  # for local / custom OpenAI-compatible endpoints
    model: str = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    embed_backend: str = os.getenv("EMBED_BACKEND", "e5")  # e5 | hash (tests only)
    embed_model: str = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-base")
    chroma_dir: str = os.getenv("CHROMA_DIR", "./chroma_db")

    # ---- Translation-memory backend ------------------------------------------
    # When zilliz_uri AND zilliz_token are set, rag.py queries Zilliz Cloud and
    # chroma_dir is ignored -- this is what the deployed Streamlit app uses, so the
    # 1.6 GB vector index never loads into the app's 2.7 GB memory budget.
    # When they are blank, rag.py falls back to the local on-disk Chroma store, so
    # build_index.py, the tests and offline dev are completely unaffected.
    zilliz_uri: str = os.getenv("ZILLIZ_URI", "")
    zilliz_token: str = os.getenv("ZILLIZ_TOKEN", "")
    zilliz_collection: str = os.getenv("ZILLIZ_COLLECTION", "legal_tm")
    # ---------------------------------------------------------------------------
    terms_db: str = os.getenv("TERMS_DB", "./terms.db")
    corpus_dir: str = os.getenv("CORPUS_DIR", "./corpus")
    top_k_examples: int = 4
    # cosine distance cut-off for translation-memory examples; tune on a dev set
    max_example_distance: float = float(os.getenv("MAX_EXAMPLE_DISTANCE", "0.22"))
    # drop "seed" in production once reviewed data exists: ALLOWED_STATUS=approved,official
    allowed_status: tuple = _csv("ALLOWED_STATUS", "approved,official,seed")
    max_chunk_chars: int = 3000
    classify_chars: int = 3500
    min_class_confidence: float = 0.65


settings = Settings()

LANGS = {"ar": "Arabic", "fr": "French", "en": "English"}
# Only these directions are supported (no Arabic <-> French).
ALLOWED_PAIRS = {("ar", "en"), ("en", "ar"), ("fr", "en"), ("en", "fr")}

DOC_CLASSES = {
    "contract": ("Contract / Agreement", "Bilateral or multilateral agreements: sale, lease, employment, service, NDA, shareholders' agreement"),
    "court_judgment": ("Court judgment / decision", "Judgments, orders, rulings, appeal decisions, with 'whereas' / attendu que / حيث إن structure"),
    "power_of_attorney": ("Power of attorney", "Procuration, mandate, وكالة: a principal authorises an agent"),
    "company_docs": ("Company documents", "Articles of association, statutes, board minutes, commercial register extracts"),
    "notarial_deed": ("Notarial / authentic deed", "Acte notarié, عقد رسمي/توثيقي: property transfer, succession, donation"),
    "civil_status": ("Civil status record", "Birth, marriage, death, divorce, family record certificates"),
    "legislation": ("Legislation / regulation", "Laws, decrees, orders, regulations with articles"),
    "administrative": ("Administrative / official certificate", "Certificates, permits, police records, official letters"),
    "other": ("Other legal", "Anything legal that fits none of the above"),
}
VALID_RECORD_CLASSES = set(DOC_CLASSES) | {"common"}