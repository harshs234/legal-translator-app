# Building the RAG — step by step

You now have clean corpus data. This guide turns it into the two RAG stores
(`terms.db` for exact terminology, `chroma_db` for aligned passages) and runs the app.

## What you have

| File (in `RAG_DATA/rag_output/processed/`) | Records | Goes to |
|---|---|---|
| `termium.jsonl` | 86,951 terms | `terms.db` (SQLite) |
| `legislation.jsonl` | 971 Act titles | `terms.db` (SQLite) |
| `un_ar_en.jsonl` | 150,000 passages | `chroma_db` (ChromaDB) |
| `un_en_fr.jsonl` | 150,000 passages | `chroma_db` (ChromaDB) |

**Already validated:** loading these files builds **155,941 bidirectional terms** into
SQLite, and lookups work both ways (e.g. "conflict of interest policy" →
"politique sur les conflits d'intérêts"). The identical-noise rows (3%) are
dropped automatically. Only the passage-embedding step still needs to run on your
machine — it uses a model my sandbox can't download.

## Steps

### 1. Put the data in place
Copy the four JSONL files into the project's corpus folder:
```
legal_translator/corpus/processed/termium.jsonl
legal_translator/corpus/processed/legislation.jsonl
legal_translator/corpus/processed/un_ar_en.jsonl
legal_translator/corpus/processed/un_en_fr.jsonl
```
(Create the `corpus/processed/` folder if it doesn't exist.)

### 2. Replace build_index.py
Replace `legal_translator/build_index.py` with the new one I sent (adds progress,
the identical-term filter, and a `--max-passages` cap for a fast first build).

### 3. Install dependencies
```powershell
cd legal_translator
pip install -r requirements.txt
```
This pulls in `sentence-transformers` + `torch` (a few hundred MB) and `chromadb`.
First run also downloads the embedding model `multilingual-e5-base` (~1 GB) — one time.

### 4. Set your API key
```powershell
copy .env.example .env
```
Open `.env` and set `ANTHROPIC_API_KEY=...`. Leave the rest as-is.

### 5. FAST first build (do this first!)
Embedding all 300k passages on CPU can take 1–3 hours. So validate the whole
pipeline with a small cap first (~5–10 min):
```powershell
python build_index.py --reset --max-passages 5000
```
You'll see terminology load instantly, then a passage progress bar with an ETA.
When it finishes:
```powershell
streamlit run app.py
```
Upload or paste a short legal document (Arabic, English or French) and translate it.
Confirm classification, glossary hits, and reference passages all appear.

### 6. FULL build (once the pilot looks right)
```powershell
python build_index.py --reset
```
This embeds everything. On CPU it's slow — if you have an NVIDIA GPU with CUDA,
`sentence-transformers` uses it automatically and it's much faster. You can also
pick a middle ground, e.g. `--max-passages 50000`.

## Notes
- **Terminology is always loaded in full** — the cap only limits passages.
- `terms.db` and `chroma_db/` are created in the project folder. To rebuild from
  scratch, delete them or pass `--reset`.
- Everything is `status=seed` (unreviewed). For production, have a legal translator
  review a sample, re-tag reviewed rows as `approved`, and set
  `ALLOWED_STATUS=approved,official` in `.env`.
- The UN English↔French set has some UN-technical (vehicle-regulation) noise. If it
  shows up in results, we can tighten the legal filter and regenerate.
