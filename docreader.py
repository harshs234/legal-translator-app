import io
import re
from docx import Document
from pypdf import PdfReader
from langdetect import detect

_AR = re.compile(r"[\u0600-\u06FF]")


def extract_text(name: str, data: bytes) -> str:
    n = name.lower()
    if n.endswith(".pdf"):
        text = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        if len(text.strip()) < 50:
            raise ValueError("No extractable text: this looks like a scanned PDF. Run OCR first (see README).")
        return text
    if n.endswith(".docx"):
        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    if n.endswith(".txt"):
        return data.decode("utf-8", errors="replace")
    raise ValueError("Supported formats: PDF, DOCX, TXT")


def detect_language(text: str) -> str:
    sample = text[:3000]
    letters = [c for c in sample if c.isalpha()]
    if letters and len(_AR.findall(sample)) / len(letters) > 0.3:
        return "ar"
    try:
        lang = detect(sample)
    except Exception:
        return "en"
    return lang if lang in ("fr", "en") else "en"
