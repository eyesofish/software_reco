# FastAPI Demo (Software Recommendation RAG)

This directory contains a FastAPI wrapper around the `software_recommend_system` package.

## Setup

```bash
cd D:\Github\software_reco\fastapi_demo
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

Or:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Parent-Child Chunking (Optional)

Parent-child chunking is feature-flagged and off by default.

Add/update these `.env` settings to enable it:

```env
ENABLE_PARENT_CHILD_CHUNKING=true
PARENT_CHUNK_SIZE=1600
PARENT_CHUNK_OVERLAP=200
CHILD_CHUNK_SIZE=400
CHILD_CHUNK_OVERLAP=80
PARENT_COLLECTION_NAME=software_recommendations_parent
```

When enabled:
- Child chunks are embedded/indexed in `software_recommendations`.
- Parent chunks are stored in `software_recommendations_parent`.
- Retrieval queries child vectors and returns parent-level content when available.

## Chroma DB Path

Set `CHROMA_DB_PATH` in `.env` to control where Chroma stores collections:

```env
CHROMA_DB_PATH=./chroma_db
```

If you are migrating from an older local Chroma layout, it can be safer to use a fresh path (for example `./chroma_db_parent_child_v2`) and run ingestion again.

## Tests

```bash
python -m pytest tests/test_parent_child_chunker.py tests/test_similarity_search_parent_child.py tests/test_vector_retry.py tests/test_retrieval_channels.py
```
