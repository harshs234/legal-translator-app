# Source registry

`status` values: **official** = taken from an official publication; **approved** = reviewed by a qualified
legal translator; **seed** = hand-written starter data, NOT reviewed. In production set
`ALLOWED_STATUS=approved,official` once you have enough reviewed data.

| Source | Pairs | Classes | Adapter | Licence / terms (verify before bulk use) |
|---|---|---|---|---|
| Your certified translation memories (TMX) | ar-en, fr-en | any | `ingest.py tmx` | yours; best source, mark `--status approved` |
| ECHR HUDOC (hudoc.echr.coe.int) | en-fr | court_judgment | `ingest.py numbered` | check HUDOC reuse terms |
| Supreme Court of Canada (decisions.scc-csc.ca) | en-fr | court_judgment | `ingest.py numbered` | check reuse terms; tag `--jurisdiction CA` |
| Canada Justice Laws bulk XML (laws-lois.justice.gc.ca/eng/XML/) | en-fr | legislation, company_docs | `ingest.py justice` | Open Government Licence - Canada (per dataset listing) |
| TERMIUM Plus open data (open.canada.ca, dataset 94fc74d6-...) | en-fr | common | `ingest.py termium` | Open Government Licence - Canada |
| IATE export (iate.europa.eu, EU Login, TBX/CSV) | en-fr | common | `ingest.py tbx` | reuse allowed with attribution "Download IATE, European Union, [year]" |
| UN Parallel Corpus v1.0 (conferences.unite.un.org/UNCorpus) | ar-en, en-fr | common | `ingest.py lines --legal-only` | free with acknowledgement; read the licence page |
| UAE Legislation / MOJ legal portal (uaelegislation.gov.ae, elaws.moj.gov.ae) | ar-en | legislation, court_judgment | export pairs to TMX/CSV, then `tmx` | verify terms; set `--jurisdiction AE` |
| Saudi Bureau of Experts (laws.boe.gov.sa) | ar-en | legislation, company_docs | same | English is "for guidance", Arabic governs; `--jurisdiction SA` |
| Qatar Al Meezan (almeezan.qa) | ar-en | legislation, court_judgment | same | official text is the Arabic; `--jurisdiction QA` |
| UNTERM (unterm.un.org) | ar-en | common | manual export to curated CSV | check terms |
| CISG authentic texts (WIPO Lex / cisg-online) | ar-en, en-fr | contract | export to TMX/CSV | UN treaty text |

Gulf-heavy Arabic: the Arabic-English official sources are UAE/Saudi/Qatar. Documents from Morocco, Algeria,
Tunisia or Egypt need their own reviewed Arabic-English data; use the `--jurisdiction` tag so Gulf data is
not applied to them.

## Ingested data (Oct 2026)

Converted via `scripts/rag_data_processor.py` and placed in `corpus/processed/`:

- `termium.jsonl` — 86,951 EN<->FR legal term pairs from TERMIUM Plus "Law and Justice"
  domain (Open Government Licence – Canada). ~3% identical src==tgt rows are dropped at build time.
- `legislation.jsonl` — 971 EN<->FR Canadian federal Act-title pairs from Justice Laws bulk XML.
- `un_ar_en.jsonl` — 150,000 AR<->EN aligned passages from UN Parallel Corpus v1.0 (legal-filtered).
- `un_en_fr.jsonl` — 150,000 EN<->FR aligned passages from UN Parallel Corpus v1.0 (legal-filtered).

All ingested as status=seed (unreviewed). Have a legal translator review a sample before
production, then re-tag reviewed rows as `approved` and set ALLOWED_STATUS=approved,official.
