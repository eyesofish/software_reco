# FastAPI Demo (Multimodal Software Recommendation RAG)

This directory contains the main multimodal RAG engine. Text questions and optional
PNG/JPEG/WebP images are normalized into one searchable query, while knowledge-base
images are captioned, embedded, and returned as cited visual evidence.

An optional native image-vector route adds CLIP shared-space text-to-image and
image-to-image retrieval without replacing the caption/OCR route.

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

Native image decoding also enforces `MULTIMODAL_MAX_IMAGE_PIXELS` (default
`16777216`) so a small compressed file cannot expand into an unbounded RGB image.

## Native Image-Vector Retrieval (Optional)

Enable the second image retrieval route:

```env
RECALL_ENABLE_IMAGE_VECTOR=true
IMAGE_EMBEDDING_MODEL=clip-ViT-B-32
IMAGE_EMBEDDING_REVISION=
IMAGE_EMBEDDING_DEVICE=cpu
IMAGE_EMBEDDING_LOCAL_FILES_ONLY=false
IMAGE_VECTOR_COLLECTION_NAME=software_recommendations_image
RECALL_IMAGE_VECTOR_TOP_K=12
MULTIMODAL_RRF_K=60
```

The feature is disabled by default so existing deployments do not download a model
or change ranking unexpectedly. For the first model download, allow remote files
with `IMAGE_EMBEDDING_LOCAL_FILES_ONLY=false`; set it to `true` after the model is
cached when offline-only startup is required.

The collection name is versioned with a model/revision fingerprint. Changing the
embedding model therefore creates a clean image collection and triggers backfill
instead of mixing incompatible vector dimensions.

The two local routes are:

1. Caption/OCR route: image -> vision description -> text embedding -> text Chroma.
2. Native route: image or text -> CLIP embedding -> cosine image Chroma.

Results are combined with reciprocal-rank fusion rather than comparing the two
models' raw distances. Uploaded image bytes and derived vectors are excluded from
LangGraph checkpoints. Image-to-image recall runs before the graph can pause and
checkpoints only ranked knowledge-base candidates; resumed HITL turns then fuse those
candidates with fresh text-to-image results.

## Image Knowledge Ingestion

Put `.png`, `.jpg`, `.jpeg`, or `.webp` files under `INGEST_PATH` (default:
`fastapi_demo/ingest_docs`). On startup, new files are incrementally processed:

1. The vision model creates a retrieval-focused description with visible text and
   technical relationships.
2. The description enters the existing chunking and text-embedding pipeline.
3. If native image retrieval is enabled, the raw image is also embedded into the
   separate cosine image collection. Existing captioned images are backfilled, and
   source-image SHA-256 changes trigger re-embedding.
   Legacy image caption rows without a source hash are re-captioned once.
4. Chroma metadata retains the original asset path.
5. Retrieval responses include `retrieved_images`, and `/api/v1/assets/...` serves
   the cited file through the Spring proxy.

Text, Markdown, and PDF ingestion continue to work unchanged.

## Synthetic Image Retrieval Benchmark

The repository includes a reproducible 30-image synthetic benchmark:

- 12 MIT-licensed software architecture diagrams.
- 10 CC BY 4.0 Rico mobile UI screenshots.
- 8 deterministically generated terminal and IDE error screenshots.

Third-party images are downloaded into the gitignored
`evaluation/image_benchmark_runtime/` directory. The committed manifest pins source
revisions and SHA-256 hashes; it also records two-pass synthetic relevance labels.
These labels are not human-verified ground truth.

Run the benchmark from `fastapi_demo`:

```bash
# The first prepare call invokes the configured Vision model and caches captions.
python -m evaluation.image_benchmark prepare

# Re-run Vision captioning only when the model or prompt intentionally changes.
python -m evaluation.image_benchmark prepare --refresh-captions

python -m evaluation.image_benchmark build-index
python -m evaluation.image_benchmark run
```

For a dependency and data-pipeline smoke test without a Vision endpoint, use
`prepare --seed-reference-captions`, followed by
`build-index --allow-reference-captions` and
`run --allow-reference-captions`. Smoke-mode metrics are marked non-evaluable and
cannot pass the keep/remove gate.

The index is isolated under the benchmark runtime directory. The run compares
caption-only retrieval, CLIP-only retrieval, and weighted reciprocal-rank fusion,
then reports Recall@1, Recall@3, MRR, category breakdowns, and a keep/remove gate.
Official runs validate the cached Vision model, endpoint, and caption-route source,
as well as the text/image embedding identities used to build the index. Pin the
image model to an immutable commit, for example:

```env
IMAGE_EMBEDDING_MODEL=clip-ViT-B-32
IMAGE_EMBEDDING_REVISION=327ab6726d33c0e22f920c83f2ff9e4bd38ca37f
```

For the first CLIP model download, set `IMAGE_EMBEDDING_LOCAL_FILES_ONLY=false`;
switch it back to `true` after the model is cached.

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
