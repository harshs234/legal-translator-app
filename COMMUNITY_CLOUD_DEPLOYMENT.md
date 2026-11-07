# Legal Translator — Streamlit Community Cloud deployment

This is a **deployment patch and guide**, not a full project archive. The uploaded `final.zip` did not include the local 3.34 GB `chroma_db/` directory, so this bundle does not contain that database. Keep your local `chroma_db/` and `terms.db` intact.

## Included patch files

- `.gitattributes`: tracks `chroma_db/` files with Git LFS.
- `.gitignore`: stops excluding `chroma_db/` and `terms.db`, while excluding local secrets, runtime folders, logs, sample legal files, and build-only data.

No application source code or database has been rewritten. Keep your existing `requirements.txt` unchanged; it also supports the non-Streamlit parts of your local project.

## 1. Apply the patch

Extract these two hidden files into the **root** of your existing `legal_translator` folder, overwriting the old `.gitignore` and adding `.gitattributes`:

- `.gitattributes`
- `.gitignore`

Make sure Windows File Explorer is showing hidden files, so the dotfiles are visible. Do not replace or delete your local `chroma_db/` or `terms.db`.

## 2. Confirm Git LFS and the local stores

In PowerShell, from the project root:

```powershell
git lfs version
git lfs install

Get-ChildItem .\chroma_db -Recurse -File |
  Select-Object FullName, Length

python -c "import chromadb; c=chromadb.PersistentClient(path='./chroma_db'); print([(x.name,x.count()) for x in c.list_collections()])"
```

Expected collection result from the current local project: `legal_tm` with `562310` records. The supplied `terms.db` passed SQLite integrity checking and has `156255` rows.

Your largest ChromaDB files were approximately 1.72 GB and 1.64 GB each. GitHub Free's Git LFS per-file maximum is 2 GB, so those two files fit individually. GitHub Free currently includes 10 GiB of LFS storage and 10 GiB of monthly LFS download bandwidth. The whole ChromaDB is about 3.34 GB, so the first Cloud clone/deploy will use a large portion of the free monthly bandwidth. Avoid unnecessary full redeploys. Also note that every new version of an LFS file consumes its full size again in storage. Consider setting a $0 LFS budget to prevent accidental overage charges; exceeding the included quota can block further LFS downloads until the next month.

## 3. Prepare Git without adding private documents

If this folder is not already a Git repository:

```powershell
git init
git branch -M main
```

Do not initialize Git again if it already exists. Add the app's runtime files explicitly, rather than using `git add .`. This helps avoid accidentally uploading actual PDFs, translated DOCX files, logs, automation databases, or other user documents:

```powershell
git add .gitattributes .gitignore requirements.txt .env.example

git add app.py classifier.py config.py docreader.py export.py jurisdiction.py llm.py prompts.py rag.py termstore.py translator.py

git add terms.db
git add chroma_db/
```

Review staged files **before committing**:

```powershell
git status --short
git lfs ls-files
```

Expected: files under `chroma_db/` appear in `git lfs ls-files`; `terms.db` appears in ordinary Git. Confirm `.env`, `.streamlit/secrets.toml`, `data/`, `inbox/`, `output/`, `processed/`, `failed/`, `corpus/processed/`, and sample PDFs/DOCX files are not staged.

If this repository already has commits or a remote, do not run `git init` again and do not overwrite an existing remote. Inspect with `git status` and `git remote -v` first.

After reviewing the staged file list:

```powershell
git commit -m "Prepare legal translator for Streamlit Cloud"
```

Create an empty GitHub repository (prefer **Private**), then add its URL and push:

```powershell
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace the URL with your own repository. If `origin` already exists, inspect it with `git remote -v` instead of adding it again.

## 4. Configure Streamlit Community Cloud

1. Sign in at https://share.streamlit.io/ using the GitHub account that owns the repository.
2. Create an app from the repository, branch `main`, entry point `app.py`.
3. In Advanced settings, choose a supported Python version. Prefer matching the Python minor version used by your working local virtual environment, if Community Cloud supports it and the dependencies have compatible wheels.
4. Open the app's **Settings → Secrets** and enter the model ID and matching provider key as root-level TOML values. Example only for Gemini:

```toml
LLM_MODEL = "gemini/gemini-2.0-flash"
GEMINI_API_KEY = "paste-your-own-key-here"
```

Use the actual model ID and provider key you intend to use. Do not put a real key in GitHub, `.env.example`, this guide, or chat. For another provider, set its matching key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `AZURE_API_KEY`) and compatible `LLM_MODEL`. For a generic endpoint, use `LLM_API_KEY` and optionally `LLM_API_BASE`.

The current `config.py` reads `os.getenv(...)`. Streamlit makes root-level secrets available as environment variables at runtime, so **no `config.py` change is required** for these root-level values. Do not place these provider keys under a nested TOML table if the app expects top-level environment variables.

## 5. Verify the deployed app

After deploy, check:

- The sidebar shows **156,255 terminology entries** (from `terms.db`).
- A test translation reports non-zero reference-passage retrieval where matching records exist.
- Cloud logs show that the embedding model loads and ChromaDB opens `./chroma_db`.
- Both Arabic ↔ English and French ↔ English directions work.

Use only non-sensitive test documents initially.

## Important feasibility and privacy limits

- Streamlit's published Community Cloud resource page lists approximately 2.7 GB maximum memory and 50 GB storage; it also says these limits can change. The app's HNSW index file is about 1.72 GB, and `intfloat/multilingual-e5-base` is about 1.0 GB in model weights. Loading the index and model alongside Python, PyTorch, ChromaDB and SQLite may exceed available RAM. This cannot be settled from file sizes alone; the first deployment/runtime logs will be needed. Do not delete, downsize, or rebuild your database just to force the first deployment.
- Community Cloud does not guarantee persistence of files written to its local filesystem. The supplied UI reads uploads in memory and returns downloads; do not add a workflow that assumes server-local files survive restarts.
- The app sends document text to the configured external LLM provider for classification and translation. Do not use confidential judicial/client documents unless your organization has approved the provider, processing terms, data region and access controls.
- Community Cloud hosts apps in the United States; region cannot be selected. Check data-residency requirements before using sensitive legal records.
- This is a deployment procedure, not a security review or production approval for judicial records.
