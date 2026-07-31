# software_reco — Multimodal RAG

Multi-service software recommendation system built around a FastAPI multimodal RAG
agent, a Spring Boot orchestration layer, and a React chat UI. It supports text,
screenshots, diagrams, and image-backed knowledge while preserving the existing
streaming, human-in-the-loop, memory, and evaluation flows.

## What This Repo Does

- Accepts software selection or implementation questions from a chat UI or API.
- Accepts up to four PNG/JPEG/WebP attachments, including image-only questions.
- Uses a configurable vision model to extract visible text and technical facts before retrieval.
- Optionally adds native CLIP shared-space text-to-image and image-to-image retrieval.
- Routes requests through a recommendation workflow with skill routing and planning.
- Retrieves evidence from local vector search, keyword search, web search, and memory.
- Ingests image knowledge assets into Chroma through caption-then-text embedding and returns the original images as citations.
- Supports human confirmation before continuing with generated sub-questions.
- Streams intermediate and final responses over Server-Sent Events.
- Persists conversations, messages, and extracted facts on the Spring side.

## Main Components

- `fastapi_demo/`
  Primary recommendation engine. Contains the FastAPI app, LangGraph-style workflow, retrieval pipeline, layered memory, startup ingestion, tests, and evaluation scripts.
- `ollama_springboot/demo/`
  Spring Boot backend that fronts the FastAPI service, stores conversation state, exposes task-oriented APIs, and relays streaming responses.
- `ollama_springboot/ollama-gui-reactjs/`
  React frontend used as the main chat UI.
- `scripts/`
  Utility scripts for local model/router startup and model installation.
- `spring_proxy/`
  Small Spring streaming proxy experiment.
- `react_frontend/`
  Minimal frontend streaming prototype.

## Architecture At a Glance

- Frontend runs on `http://127.0.0.1:3000`
- Spring Boot runs on `http://127.0.0.1:8080`
- FastAPI runs on `http://127.0.0.1:8000`
- Spring Boot calls FastAPI for recommendation execution and session-state sync.
- FastAPI incrementally ingests new text, PDF, and image files from `fastapi_demo/ingest_docs`.
- When native image retrieval is enabled, FastAPI stores one raw-image embedding per
  knowledge image in a separate cosine Chroma collection and backfills images whose
  caption chunks were indexed previously. Caption, metadata, model identity, and
  source-image SHA-256 changes refresh existing rows.
- Legacy image caption rows without a source SHA-256 are re-captioned once so future
  in-place asset changes can be detected safely.
- Uploaded image bytes are processed in memory; Spring persists only a text summary of attachment names, and the React autosave omits raw base64 data.
- Native image decoding rejects images above `MULTIMODAL_MAX_IMAGE_PIXELS` even when their compressed byte size is small.

## Notable Features

- Skill router with domain-specific profiles such as software comparison, architecture design, implementation guidance, and quick fact QA.
- Planner-driven retrieval flow with sub-question generation.
- Optional LLM-driven tool-calling loop (`AGENT_LOOP_ENABLE`) that lets the model
  choose which retrieval tools to call through the `ToolRegistry`, with step and
  tool-call budgets and automatic fallback to the static retrieval pipeline.
- Evidence quality evaluation feeding the coverage gate, with a deterministic
  heuristic by default and an optional LLM judge (`EVIDENCE_EVAL_USE_LLM`).
- LLM call governance: bounded timeout, exponential backoff on transient errors
  only, and a per-scene token/cost ledger.
- Human-in-the-loop confirm and confirm-stream endpoints.
- Layered memory with fact, episodic, semantic, and working-memory retrieval.
- Parent-child chunking support for ingestion and retrieval.
- Evaluation scripts for retrieval benchmarks and end-to-end runs, including a
  reproducible caption-only vs. CLIP-only vs. RRF image-retrieval benchmark.
- Runtime logs written under service-local `.runtime/` directories, including `fastapi_demo/.runtime/` for startup, embedding, Tavily, and LLM invocation tracing.

## Prerequisites

- Python 3
- Java 21
- Node.js with `npm` or `yarn`
- An OpenAI-compatible or DashScope-compatible LLM/embedding setup for the FastAPI service
- An OpenAI-compatible vision model endpoint that accepts `image_url` message parts

Optional but useful:

- `TAVILY_API_KEY` for web recall
- PostgreSQL if you want to run Spring Boot with the `postgres` profile instead of the default in-memory H2 database

## Quick Start

### Windows one-command startup

From the repository root:

```powershell
.\start-all.ps1 -InstallDeps
```

Alternatives:

```cmd
start-all.cmd
```

```bash
./start-all.sh
```

The startup script launches:

- FastAPI on port `8000`
- Spring Boot on port `8080`
- React frontend on port `3000`

It also writes started process IDs to `.runtime/started-processes.txt`.

## Manual Startup

### 1. Configure environment

Review and update:

- `fastapi_demo/.env`
- `fastapi_demo/software_recommend_system/.env`

Important settings used by the FastAPI side include:

- `OPENAI_API_KEY` or `DASHSCOPE_API_KEY`
- `OPENAI_BASE_URL`
- `LLM_MODEL`
- `VISION_MODEL`, `VISION_BASE_URL`, and optionally `VISION_API_KEY`
- `RECALL_ENABLE_IMAGE_VECTOR`, `IMAGE_EMBEDDING_MODEL`, and
  `IMAGE_EMBEDDING_LOCAL_FILES_ONLY`
- `TAVILY_API_KEY`
- `CHROMA_DB_PATH`
- `INGEST_PATH`
- `ENABLE_PARENT_CHILD_CHUNKING`
- `RECALL_CHANNEL_TIMEOUT_SECONDS`

The default vision model is `qwen-vl-max`. `VISION_API_KEY` falls back to
`DASHSCOPE_API_KEY`, then `OPENAI_API_KEY`.

### Recall timeout

The five recall channels (vector, image vector, keyword, web, memory) run in
parallel and share one wall-clock budget, `RECALL_CHANNEL_TIMEOUT_SECONDS`
(default `20`). Channels still running when it expires are abandoned, log
`search.retrieve.channel.timeout`, and contribute no documents; the request
continues with whatever the other channels returned and logs
`search.retrieve.degraded`. This bounds tail latency so one slow dependency
(typically the external web search) cannot stall the whole turn. Set it to `0`
to disable the budget and wait for every channel.

### Optional native image-vector route

The existing route captions images and searches those captions with the text
embedding model. Enable the complementary native route with:

```env
RECALL_ENABLE_IMAGE_VECTOR=true
IMAGE_EMBEDDING_MODEL=clip-ViT-B-32
IMAGE_EMBEDDING_REVISION=
IMAGE_EMBEDDING_DEVICE=cpu
IMAGE_EMBEDDING_LOCAL_FILES_ONLY=false
IMAGE_VECTOR_COLLECTION_NAME=software_recommendations_image
```

On the first connected startup, `IMAGE_EMBEDDING_LOCAL_FILES_ONLY=false` allows
SentenceTransformers to download the model. Set it back to `true` for offline-only
startup after the model is cached.

The configured collection name is treated as a base name and receives a stable
model/revision fingerprint suffix, so changing embedding spaces cannot mix
incompatible vectors.

The native route embeds both text subqueries and uploaded images in the same space.
It queries only knowledge images, fuses text/image query results with reciprocal-rank
fusion, then RRF-fuses that visual route with the existing caption/text-vector route.
Raw uploaded image bytes and derived vectors are not written to LangGraph
checkpoints. Image-to-image retrieval runs during the initial request and stores only
the ranked knowledge-base image candidates in graph state. HITL resume can therefore
continue on another worker without retaining user-derived vectors.

### 2. Start FastAPI

```powershell
cd fastapi_demo
python -m pip install -r requirements.txt
python main.py
```

Alternative:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```text
http://127.0.0.1:8000/health
```

### 3. Start Spring Boot

```powershell
cd ollama_springboot\demo
.\mvnw.cmd spring-boot:run
```

Notes:

- Default profile uses in-memory H2.
- The optional `postgres` profile reads `DB_URL`, `DB_USER`, and `DB_PASSWORD`.
- H2 console is enabled by default at `http://127.0.0.1:8080/h2-console`.

### 4. Start the React frontend

```powershell
cd ollama_springboot\ollama-gui-reactjs
npm install
npm run start
```

Or:

```powershell
yarn
yarn start
```

## Security & Operations

### API key authentication (FastAPI)

All `/api/v1/*` endpoints require an `X-API-Key` header when `API_KEY` is set
in `fastapi_demo/.env`. Leave `API_KEY=""` for dev (no auth). `/initialize-db`
is gated behind `ADMIN_API_KEY` (falls back to `API_KEY` if unset).

Generate keys:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Rate limiting

A default `30/minute` per-IP limit is applied to every FastAPI route via
`slowapi`. Tune via `RATE_LIMIT_RECOMMEND` in `fastapi_demo/.env`.

### Secret-scan pre-commit hook

The repo ships a `.pre-commit-config.yaml` with `gitleaks` and standard
pre-commit hygiene hooks. Enable once:

```bash
pip install pre-commit
pre-commit install
```

### Rotating leaked keys

If an API key is committed (or shared in chat / screenshots), treat it as
compromised: rotate it in the provider dashboard (DashScope, Tavily, OpenAI)
**before** removing it from the file. Removal from disk does not retroactively
secure the key.

## Important API Endpoints

### FastAPI

- `POST /api/v1/recommend`
- `POST /api/v1/recommend/stream`
- `POST /api/v1/recommend/confirm`
- `POST /api/v1/recommend/confirm/stream`
- `GET /api/v1/session-state/{session_id}`
- `PUT /api/v1/session-state/{session_id}`
- `POST /api/v1/initialize-db` (admin only)
- `GET /api/v1/assets/{path}` for retrieved image evidence

`/recommend` and `/recommend/stream` accept an optional `images` array:

```json
{
  "query": "What does this architecture diagram imply?",
  "images": [
    {
      "name": "architecture.png",
      "media_type": "image/png",
      "data_url": "data:image/png;base64,..."
    }
  ]
}
```

Successful responses may include `retrieved_images` with `doc_id`, `filename`,
`media_type`, `url`, `caption`, and `score`.

### Spring Boot

- `POST /api/chat`
- `POST /api/chat/stream`
- `POST /api/chat/confirm`
- `POST /api/chat/confirm/stream`
- `POST /api/v1/conversations`
- `POST /api/v1/recommend`
- `GET /api/conversations`
- `GET /api/session-state/{conversationId}`

## Testing And Evaluation

FastAPI tests are under `fastapi_demo/tests/`. They cover areas such as routing, planner logic, retrieval channels, memory store behavior, parent-child chunking, and evaluation metrics.

Run the test suite with the standard library test runner:

```powershell
cd fastapi_demo
python -m pytest
```

Cross-stack checks:

```bash
cd ollama_springboot/demo && sh mvnw test
cd ollama_springboot/ollama-gui-reactjs && npm test -- --watchAll=false --runInBand
cd ollama_springboot/ollama-gui-reactjs && npm run build
```

Evaluation utilities live in `fastapi_demo/evaluation/`, including:

- retrieval benchmark scripts
- smoke and real end-to-end evaluation scripts
- LangSmith export/evaluation helpers

## Repository Notes

- `fastapi_demo/main.py`, `spring_proxy/`, and `react_frontend/` are smaller streaming-focused experiments and are not the main production path in this repository.
- The main end-to-end path is: React UI -> Spring Boot -> FastAPI -> retrieval/LLM pipeline.
- Additional project notes and Chinese-language startup/process docs are in files such as `启动教程.md`, `workflow.txt`, and the design guides in the repository root.

## Recommended Reading Inside The Repo

- `fastapi_demo/README.md`
- `启动教程.md`
- `workflow.txt`
- `progress_report.md`
