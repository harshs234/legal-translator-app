"""Free-text jurisdiction (from the classifier or the user) -> ISO-style code."""
_MAP = {
    "ae": "AE", "uae": "AE", "united arab emirates": "AE", "emirates": "AE", "dubai": "AE", "abu dhabi": "AE",
    "sa": "SA", "ksa": "SA", "saudi arabia": "SA", "saudi": "SA",
    "qa": "QA", "qatar": "QA", "ma": "MA", "morocco": "MA", "maroc": "MA",
    "dz": "DZ", "algeria": "DZ", "tn": "TN", "tunisia": "TN", "eg": "EG", "egypt": "EG",
    "lb": "LB", "lebanon": "LB", "jo": "JO", "jordan": "JO", "kw": "KW", "kuwait": "KW",
    "bh": "BH", "bahrain": "BH", "om": "OM", "oman": "OM", "iq": "IQ", "iraq": "IQ",
    "ca": "CA", "canada": "CA", "fr": "FR", "france": "FR", "gb": "GB", "uk": "GB", "united kingdom": "GB",
    "un": "UN", "united nations": "UN", "eu": "EU", "european union": "EU", "int": "INT", "international": "INT",
}


def normalize_jurisdiction(s: str) -> str:
    return _MAP.get((s or "").strip().lower(), "")
