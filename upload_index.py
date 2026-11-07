"""One-time: push the local Chroma index to a private Hugging Face dataset repo.

Space repos are capped at 1 GB, so the 3.3 GB chroma_db cannot sit beside the app.
It goes in a dataset repo instead (free accounts get 100 GB of private Hub
storage), and the Space Dockerfile pulls it at build time.

Run from the project root with venv313 active:

    hf auth login                 # paste a WRITE-scoped token
    python upload_index.py

Resumable: if it drops, re-run the same command. Already-committed files are
skipped and already-uploaded chunks are deduplicated, so almost nothing is
re-sent. No local state is kept, so you can even resume from another machine.

Nothing here reads, rebuilds or re-embeds the index -- it is a byte-for-byte
upload of the directory you already have.
"""
import os
from huggingface_hub import HfApi

REPO_ID = os.getenv("INDEX_REPO", "harshs234/legal-translator-index")
LOCAL_DIR = os.getenv("CHROMA_DIR", "./chroma_db")

if not os.path.isdir(LOCAL_DIR):
    raise SystemExit(f"Not found: {LOCAL_DIR} (run this from the project root)")

total = sum(
    os.path.getsize(os.path.join(root, f))
    for root, _, files in os.walk(LOCAL_DIR)
    for f in files
)
print(f"Local index : {LOCAL_DIR}  ({total / 1024**3:.2f} GB)")
print(f"Destination : {REPO_ID}  (dataset, private)")

api = HfApi()
api.create_repo(REPO_ID, repo_type="dataset", private=True, exist_ok=True)
api.upload_folder(
    folder_path=LOCAL_DIR,
    repo_id=REPO_ID,
    repo_type="dataset",
    commit_message="ChromaDB legal_tm index (562,310 records)",
)
print(f"\nDone. Set INDEX_REPO={REPO_ID} as the Dockerfile build arg if you renamed it.")