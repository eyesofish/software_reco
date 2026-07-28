# FastAPI Demo (Multimodal Software Recommendation RAG)

This directory contains the main multimodal RAG engine. Text questions and optional
PNG/JPEG/WebP images are normalized into one searchable query, while knowledge-base
images are captioned, embedded, and returned as cited visual evidence.

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

## Multimodal Configuration

Configure an OpenAI-compatible vision endpoint:

```env
VISION_MODEL=qwen-vl-max
VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_API_KEY=
MULTIMODAL_MAX_IMAGES=4
MULTIMODAL_MAX_IMAGE_BYTES=5242880
MULTIMODAL_MAX_TOTAL_IMAGE_BYTES=12582912
```

`VISION_API_KEY` falls back to `DASHSCOPE_API_KEY`, then `OPENAI_API_KEY`.
The vision model must accept chat content containing `image_url` parts.

The request contract is backward compatible:

```json
{
  "query": "Explain the error in this screenshot",
  "images": [
    {
      "name": "error.png",
      "media_type": "image/png",
      "data_url": "data:image/png;base64,..."
    }
  ]
}
```

Image-only requests are supported. Raw image data is validated in memory and is not
written to the FastAPI session store.

## Image Knowledge Ingestion

Put `.png`, `.jpg`, `.jpeg`, or `.webp` files under `INGEST_PATH` (default:
`fastapi_demo/ingest_docs`). On startup, new files are incrementally processed:

1. The vision model creates a retrieval-focused description with visible text and
   technical relationships.
2. The description enters the existing chunking and text-embedding pipeline.
3. Chroma metadata retains the original asset path.
4. Retrieval responses include `retrieved_images`, and `/api/v1/assets/...` serves
   the cited file through the Spring proxy.

Text, Markdown, and PDF ingestion continue to work unchanged.

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
python -m pytest
```
