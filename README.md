# software_reco

Multi-service software recommendation system built around a FastAPI RAG agent, a Spring Boot orchestration layer, and a React chat UI. The repository is structured for local experimentation with streaming responses, human-in-the-loop confirmation, layered memory, document ingestion, and evaluation tooling.

## What This Repo Does

- Accepts software selection or implementation questions from a chat UI or API.
- Routes requests through a recommendation workflow with skill routing and planning.
- Retrieves evidence from local vector search, keyword search, web search, and memory.
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
- FastAPI auto-ingests files from `fastapi_demo/ingest_docs` when the Chroma vector store is empty.

## Notable Features

- Skill router with domain-specific profiles such as software comparison, architecture design, implementation guidance, and quick fact QA.
- Planner-driven retrieval flow with sub-question generation.
- Human-in-the-loop confirm and confirm-stream endpoints.
- Layered memory with fact, episodic, semantic, and working-memory retrieval.
- Parent-child chunking support for ingestion and retrieval.
- Evaluation scripts for retrieval benchmarks and end-to-end runs.
- Runtime logs written under service-local `.runtime/` directories, including `fastapi_demo/.runtime/` for startup, embedding, Tavily, and LLM invocation tracing.

## Prerequisites

- Python 3
- Java 21
- Node.js with `npm` or `yarn`
- An OpenAI-compatible or DashScope-compatible LLM/embedding setup for the FastAPI service

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
- `TAVILY_API_KEY`
- `CHROMA_DB_PATH`
- `INGEST_PATH`
- `ENABLE_PARENT_CHILD_CHUNKING`

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

## Important API Endpoints

### FastAPI

- `POST /api/v1/recommend`
- `POST /api/v1/recommend/stream`
- `POST /api/v1/recommend/confirm`
- `POST /api/v1/recommend/confirm/stream`
- `GET /api/v1/session-state/{session_id}`
- `PUT /api/v1/session-state/{session_id}`
- `POST /api/v1/initialize-db`

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
python -m unittest discover -s fastapi_demo\tests -p "test_*.py"
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
