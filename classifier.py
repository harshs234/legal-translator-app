import json
import re
from config import DOC_CLASSES, settings
from llm import complete
from prompts import CLASSIFY_SYSTEM, classify_user


def classify(text: str) -> dict:
    """Returns {doc_class, confidence, jurisdiction, reason}. Low confidence -> UI asks the human."""
    raw = complete(CLASSIFY_SYSTEM, classify_user(text[: settings.classify_chars]), max_tokens=300)
    try:
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:
        data = {}
    cls = data.get("doc_class")
    if cls not in DOC_CLASSES:
        cls, data["confidence"] = "other", 0.0
    return {
        "doc_class": cls,
        "confidence": float(data.get("confidence", 0.0)),
        "jurisdiction": data.get("jurisdiction", "unknown"),
        "reason": data.get("reason", ""),
    }
