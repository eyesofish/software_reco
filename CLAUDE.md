# Software Reco

FastAPI-based RAG application for software engineering resource recommendations.

## Skills

### `/project-upgrade`
Systematically upgrade this codebase from rough prototype to production grade.
Runs ARC's audit → prioritize → implement → test → ship pipeline.

Usage:
- `/project-upgrade --level=quick` — Critical fixes only
- `/project-upgrade --level=standard` — Full audit + fixes + tests
- `/project-upgrade --level=deep` — Everything + architecture + hardening

ARC plugin root: `.arc/` (local clone of github.com/howells/arc)

## Tech Stack

- **Backend:** Python FastAPI (fastapi_demo/)
- **Frontend:** React (react_frontend/)
- **Java:** Spring Boot + Ollama (ollama_springboot/)
- **Vector DB:** ChromaDB
- **RAG:** LangChain + custom parent-child chunking

## Key Directories

| Directory | Purpose |
|-----------|---------|
| `fastapi_demo/` | Main FastAPI app + evaluation scripts |
| `fastapi_demo/app/api/v1/handlers/` | Route-handler helpers (normalizers, orchestrator, agent singleton) |
| `fastapi_demo/software_recommend_system/agent_tools/` | Tool Protocol + registry + hook seams (RAG → agent seam) |
| `react_frontend/` | React UI |
| `ollama_springboot/` | Ollama + Spring Boot proxy |
| `spring_proxy/` | Spring proxy service |
| `.claude/skills/` | Custom Claude Code skills |

## Architecture seams worth knowing

- **Tool registry** (`agent_tools/registry.py`): the four retrievers (vector / keyword / web / memory) are now wrapped as `Tool` subclasses, dispatched through `ToolRegistry.execute()`, with automatic `tool.invoke.{start,done,fail}` structured logs and pluggable `before_tool_call` / `after_tool_call` hooks. The static `retrieve_node` still calls `recall_*` directly — the registry is the seam for a future LLM-driven tool-calling agent loop, not a behavior change today.
- **Auth dependency** (`app/api/v1/auth.py`): `X-API-Key` middleware applied router-wide; `/initialize-db` uses a separate admin router.
- **Route handlers split**: `routes.py` keeps the FastAPI surface; `handlers/normalizers.py` owns pure helpers; `handlers/recommend_orchestrator.py` owns `execute_recommend_turn` and task helpers.

## Reference

- ARC rules and references: `.arc/rules/`
- Project change map: `PROJECT_CHANGE_MAP.md`
- Architectural gap analysis: `software_reco_interview_questions.md`
