# Local checks and performance evaluation

Run from the repository root with PowerShell 5.1 or 7. The script uses the root
`.venv` when present, otherwise the active `python`. Override it with `-Python`.
Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r fastapi_demo/requirements.txt
.\.venv\Scripts\python.exe -m pip install -e 'fastapi_demo[dev]'

.\scripts\run_checks.ps1 -Mode offline
.\scripts\run_checks.ps1 -Mode baseline
.\scripts\run_checks.ps1 -Mode real -Baseline <absolute-or-root-relative-baseline.json>
```

Linux/macOS and CI can call the Python entry points from `fastapi_demo`:

```sh
python -m evaluation.local_checks
python -m evaluation.performance_runner --mode baseline
python -m evaluation.performance_runner --mode real --baseline /path/to/baseline.json
```

`-Output` / `--output` names a new report directory; do not reuse an existing
directory. Generated reports and runtime state are ignored by Git. The CLI prints
`REPORT=...` at completion. Exit codes are 0 (pass), 1 (functional/performance
failure), and 2 (unusable environment/configuration/baseline).

## Offline checks

Ruff and pytest are hard gates. Mypy runs with the existing strict settings but is
advisory. All checks run even if lint fails, so JUnit, the slowest 20 tests and the
summary remain available. Output is under `evaluation/reports/checks/<run>/`.
Existing unrelated lint failures are not suppressed.
Advisory type checking has a 120-second timeout; timeout code 124 is recorded and
the test stage still runs. Owned child processes are cleaned up on timeout.

The offline pytest process rejects external DNS and socket connections. Loopback
is permitted for real HTTP/SSE integration tests. Elasticsearch integration tests
remain explicitly skipped. A separate Uvicorn child process keeps the actual
FastAPI routes, graph, execution control, Chroma query and reranking logic, while
replacing model/embedding clients with deterministic adapters. Neural reranking
is disabled in this offline profile; deterministic reranking remains active.
The model-failure, timeout, capacity and disconnect tests use test-only markers
under the evaluation bootstrap, never the production app.

JUnit and logs are at the report root; HTTP scenario JSON, CSV, Markdown, traces
and server logs are under `pytest-temp/**/http/`. Offline measurements are not real
model latency and cannot be used as a real baseline.

For an isolated 30-scenario smoke report without running lint/unit tests:

```sh
python -m evaluation.performance_runner --mode offline
```

## Real evaluation

Configure model/embedding credentials and endpoints in `fastapi_demo/.env` or
environment variables. Environment variables override `.env`. The selected Python
environment must already contain project and dev dependencies. Real mode uses
the configured LLM, embedding, router, planner and reranking settings; local model
files/endpoints must be available. Neural reranking may load/download its configured
model when local-files-only is disabled. No answer-scoring LLM is used.

The fixed profile enables vector and memory recall; disables web, Elasticsearch,
image recall, parent-child indexing, LLM evidence judging and LangSmith; and raises
the evaluation rate limit to 10000/minute. These overrides appear in the fingerprint.
The corpus is pre-embedded/indexed in an isolated Chroma directory, not the user's
index. The ten short source passages are evaluation fixtures, not a documentation
benchmark. This measures performance and protocol correctness, not factual quality.

Each round starts a fresh server, corpus, session store, memory store and SQLite
checkpointer. Startup/indexing, one warmup per group and memory preparation are
reported separately. Formal requests run sequentially: 10 direct, 10 RAG, 5 HITL,
5 memory scenarios. HITL first calls `/api/v1/recommend`, checks the interruption,
then calls `/api/v1/recommend/confirm/stream` and checks the confirmation result.
Other formal requests use `/api/v1/recommend/stream`.

HTTP request time ends after the response stream closes, not at response headers.
TTFT begins at request send and ends at the first nonempty answer `token`; `meta`,
node events and whitespace tokens do not qualify. Final-only memory paths have
null TTFT. HITL scenario time adds its two request durations, excluding the gap;
its scenario TTFT adds the initial request duration to the resume request's TTFT.
Both requests have distinct request IDs, sharing the same session ID.

Non-2xx HTTP, SSE `error`, failed terminal status, missing terminal event/expected
token, empty answer, wrong branch, missing RAG evidence and failed memory/confirmation
assertions are failures. Recorded dependency errors or routing fallbacks also fail;
a successful-looking fallback response must not masquerade as a speed improvement.
Failed requests are listed separately and always fail the run. Successful-case
distributions show eligible sample counts. P95/P99 from 30 cases are descriptive.

Reports under `evaluation/reports/performance/<run>/` contain `results.json`,
`cases.csv`, `report.md`, and per-round `spans.jsonl`, `setup.json`, and `server.log`.
JSON includes setup, warmups, memory preparation, formal request measurements and
stage observations. Parallel/nested stage durations overlap and must not be added.
The runner never retries failed scenarios; configured model-internal retries are
preserved and recorded where existing governance emits them.

The HTTP middleware returns `X-Request-Id` (echoing a valid client ID or generating
one), and CORS exposes that header. Response bodies retain their existing shapes.
Stage recording is opt-in through `PERFORMANCE_TRACE_FILE`; evaluation sets it to
the isolated round's JSONL path. `PERFORMANCE_RUNTIME_DIR` relocates the existing
runtime log files for evaluation. Normal application startup keeps its usual log
directory when that variable is absent. Diagnostic events without durations are
retained for failure detection and excluded from timing distributions.

## Baselines and gates

Baseline mode runs three full successful rounds (90 measured scenarios plus
warmups/preparation). It writes a new `baseline.json`, using the median of each
round's P50/P95/P99; it never overwrites/promotes another baseline. Select a
baseline explicitly for real comparison; missing/mismatched baselines fail before
model calls. No baseline is generated from incomplete or failed rounds.

Compatibility requires the same machine/OS/CPU, Python/dependency versions, model
and endpoint configuration, scenario/corpus hash, warmups, timeout/iteration and
concurrency settings. The Git commit is recorded but may change, allowing code
regression comparisons. Credentials and transient runtime paths are excluded.
An unchanged remote model alias cannot prove identical provider weights; record
a new baseline after an upstream deployment change.

Either **scenario P50** or **TTFT P50** fails the run when its increase is both
strictly greater than 20% and strictly greater than 1000 ms. Equality does not fail.
P95/P99 increases over both 30% and 2000 ms are warnings only. Eligible TTFT sample
counts must match. Per-category timings are shown for diagnosis, not independent
gates in v1. A combined P50 can conceal a regression in a small category; consult
the per-category and slowest-case rows when reviewing results.

The ordinary CI job uses the same offline CLI and uploads artifacts even on
failure. The manual performance workflow requires a self-hosted runner labeled
`software-reco-performance` with the same environment/model access as its baseline.
Provision dependencies separately; don't rebuild that environment per comparison.
For real mode, supply the numeric workflow run ID containing the explicit
`performance-baseline` artifact. Baseline mode creates that artifact. Optional
repository variables: `PERFORMANCE_PYTHON`, `PERFORMANCE_OPENAI_BASE_URL`; optional
secrets: `PERFORMANCE_OPENAI_API_KEY`, `PERFORMANCE_DASHSCOPE_API_KEY`. Other settings
must come from the provisioned runner environment. Do not use a hosted-machine
baseline for local performance comparisons.

Java forwarding, browser rendering, image workloads and load tests are outside v1.
The existing 3x100 LangSmith quality evaluation scripts remain independent.


## Agent latency optimizations

`generic_rag` and `quick_fact_qa` answer directly from evidence and skip model
candidate generation. Recommendation skills still generate candidates. Known
chat-first requests without technical/RAG/drawing intent skip model normalization.

`SUBQUERY_MAX_CONCURRENCY=3` bounds independent subquery tasks per graph execution
(valid range 1–8). Tasks register in input order in bounded batches; results and
checkpoint replay preserve subquery identity. Each request still uses the existing
run deadline/cancellation control. Different retrieval channels run in parallel.

`RECALL_WEB_POLICY=auto` searches web immediately for explicit external/freshness
queries. Otherwise local vector/image/keyword recall runs first; web runs only
when fewer than `RECALL_WEB_MIN_LOCAL_DOCS=2` unique documents meet the heuristic
`RECALL_WEB_MIN_LOCAL_SCORE=0.5`. Memory is not factual coverage. These raw channel
scores are not calibrated confidence; validate quality on your corpus and tune
thresholds. `always` restores previous fan-out, `never` disables shared and tool
web recall, and `RECALL_ENABLE_WEB=false` remains the master switch. The original
request supplies web intent even if normalization removes it; explicit offline
requests also bypass the optional model-driven retrieval loop. Local and fallback
web phases share one channel timeout, so fallback cannot double that budget.

Static graph subqueries defer reranking until global deduplication. Global neural
rerank now obeys `RETRIEVAL_ENABLE_RERANK`; disabled mode keeps lightweight linear
fusion. Standalone shared retrieval keeps its own rerank behavior when enabled.
Optional model-driven tools may perform additional ranking by their own design.
`PERFORMANCE_TRACE_FILE` includes `retrieval.rerank`, cold
`retrieval.rerank.model_load` (including library import), and
`retrieval.rerank.predict` spans with model/document counts. Nested durations
must not be summed.

Run the reproducible graph probe from `fastapi_demo`:

```powershell
..\.venv\Scripts\python.exe -m evaluation.latency_probe --cached-reranker
```

The graph probe stubs answer generation and injects recall delays. Its serial,
parallel, and web-policy comparisons are scheduling evidence, not measured remote
model improvements. `--cached-reranker` separately times the real local CPU model
using only cached files; missing models are reported unavailable, with no download.
The probe writes `evaluation/reports/latency/<UTC>/results.json`. Set
`PERFORMANCE_TRACE_FILE` separately to retain detailed spans.

Measured on this workspace, 2026-10-09 (5 measured rounds after one warmup):
- Three 150 ms local recalls: serial P50 497.5 ms, concurrency 3 P50 181.4 ms.
- Concurrency 3, 300 ms web delay: always-web P50 335.3 ms vs auto 181.4 ms;
  web calls 18 vs 0 including warmup, with sufficient fixture local evidence.
- Real cached bge-reranker-base on CPU, 30 short documents: full cold rerank
  25,891.6 ms, warm P50 2,667.4 ms; linear warm P50 8.6 ms.

These local fixture measurements exclude production corpus/traffic, model answer
latency and ingestion. Re-run on the deployment host. Real HTTP benchmarking
still requires a working configured Router. A fresh configured Router probe on
2026-10-09 again returned fallback after 3,479.4 ms; that is service failure overhead,
not a valid successful-routing baseline.

`execution.cleanup` is emitted after graph teardown releases the session and
worker capacity. A node finishing alone does not prove a cancelled request has
released its worker. HTTP capacity/disconnect tests wait for this completion
signal, then verify that a new request succeeds.
