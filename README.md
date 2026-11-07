---
title: Legal Translator
emoji: ⚖️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8501
pinned: false
short_description: Arabic and French to/from English legal document translation with terminology + translation-memory RAG
---

# Legal Document Translator

Arabic ⇄ English and French ⇄ English legal translation.
Classify → retrieve class-specific terminology and approved parallel passages → translate → QA flags → review.

## Runtime layout

| Asset | Size | Where it lives |
|---|---:|---|
| Translation memory (`chroma_db`, 562,310 records) | 3.3 GB | Private dataset repo, baked into the image at build time |
| Terminology (`terms.db`, 156,255 records) | 45 MB | This repo |
| Embeddings (`intfloat/multilingual-e5-base`) | ~1.0 GB RAM | Baked into the image |

Space repos are capped at 1 GB, which is why the index is not stored here.

## Required Space secrets

Settings → Variables and secrets:

| Name | Scope | Purpose |
|---|---|---|
| `HF_TOKEN` | secret | Read-scoped token; the Dockerfile uses it at build time to pull the private index |
| `GEMINI_API_KEY` | secret | Translation provider |
| `LLM_MODEL` | variable | e.g. `gemini/gemini-3.5-flash-lite` |

<!-- Paste the body of your existing README.md below this line. -->