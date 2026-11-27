# Routing, retrieval, and knowledge snapshots

The default is `ROUTING_DEPLOYMENT_MODE=shadow`, traffic `0`, and semantic session assignment disabled. Existing explicit conversation requests retain the baseline execution path. A configured Jev key enables observation; it does not enable automatic assignment or dynamic execution.

## Routing contract

`intent-routing.v1` separates `task_relation` (`continue`, `new`, `uncertain`), `request_type` (`query`, `action`, `other`, `uncertain`), and answering `mode` (`rag`, `direct`, `hitl`). An action label does not grant permission or add an external action tool.

Spring checks ownership and conflicts among `conversation_id`, `session_id`, and `thread_id` before reading the candidate context or creating any conversation, message, facts, or task. Explicit IDs bind the target. Auto mode considers only the current conversation; it does not search other conversations. A new backend task is still created for each ordinary request, including a continued conversation.

For an unbound target, Jev accepts a decision at probability >=0.90. Probability >=0.60 and <0.90 receives one review by the existing router LLM, whose acceptance threshold is 0.80. Lower scores or unresolved labels ask the user. Review calls share a maximum of three per task. Transport failure is recorded separately: unknown attribution asks for a target; a known target uses conservative RAG. Structured targets cannot be changed by either model.

The UI defaults to the current conversation. Auto attribution is optional. Attribution clarification occurs before persistence. Execution clarification is a separate `PENDING_INPUT` task state and resumes the same checkpoint through `/api/v1/recommend/clarify/stream`; `/api/v1/recommend/tasks/{taskId}/cancel` cancels a waiting task. A conversation cannot start another task while its current task is waiting.

## Bounded dynamic execution

Set `DYNAMIC_ROUTING_ENABLE=true` only for the admitted live candidate cohort. Decision nodes checkpoint before interrupt nodes. Jev selects only an allowed step after planning or evidence assessment: retrieve, refine, answer, or clarify. The graph restricts answering to adequate evidence except at a hard limit, where the response explicitly marks incomplete evidence. Fact QA goes directly to answering; comparison and analysis can generate candidates.

Coverage counts distinct supported objectives, not retrieved documents. Refinement searches uncovered objectives and retains already supported evidence. Bounds are one refinement, two execution clarifications, and the request's retrieval-round limit (default three). Active elapsed time includes preflight, multimodal preparation, models, retrieval, and previous executions; user waiting time is excluded. Resume receives only the remaining time budget. Disabling rollout does not prevent an already waiting task from resuming.

## Query expansion

The canonical query stays intact. A strict JSON proposal supplies `original_query`, `preserved_constraints`, `additions`, and `confidence`; every addition has `text`, `kind`, and `source_span`. Validation rejects malformed output, extra fields, low confidence, changed original text, conflicting entities/numbers, ungrounded assertions, and positive expansions of negated terms. Synonyms use a conservative vocabulary; contextual references require a referent in supplied prior context.

Both the original and validated expanded query run keyword and vector retrieval. Each channel rescoring step uses the same original query and scoring method, neutralizing raw query-dependent scores. Expansion is adopted only if it adds new evidence and improves a fixed-top-k score; original hits remain in the merge. Otherwise retrieval uses the original results. These are conservative guards, not a claim that arbitrary semantic equivalence can be proven automatically.

Application retrieval always passes through this protected pipeline. The older experimental `AGENT_LOOP_ENABLE` free-form query/tool loop is no longer selected by `retrieve_node`; dynamic path selection uses `DYNAMIC_ROUTING_ENABLE`. Successive user clarifications accumulate, so later answers cannot remove earlier conditions or negations.

## Knowledge snapshots

Document revisions are immutable and share the existing text collection across snapshots; no collection is created per version. Existing parent-chunk and native-image auxiliary indexes remain in use. Image vectors have their own embedding space/dimension and cannot share the text vector collection. All indexes follow the same Manifest. The five metadata fields are:

| Field | Meaning |
|---|---|
| `document_id` | Stable logical document identity |
| `version` | Immutable revision identity |
| `snapshot_id` | Snapshot in which the revision was created |
| `status` | Revision artifact readiness |
| `chunk_id` | Immutable chunk identity including its revision |

A SQLite Manifest records each published snapshot's `document_id -> version` mapping and a single active pointer. Unchanged revisions are reused. Therefore filtering only `metadata.snapshot_id == current_snapshot` would incorrectly exclude unchanged documents; readers use the Manifest membership. `kb_snapshot_id` in graph state is separate from the routing input's `snapshot_id`.

Ingestion stages revisions, writes required indexes, records readiness, and atomically publishes only after all required writes succeed. Compare-and-swap publication rejects competing writers based on a stale active snapshot. Readers pin once before multimodal retrieval and keep that pin across parallel recalls, refinement, and checkpoint resume. Failed or staged snapshots cannot be selected. Existing rows are adopted before the first versioned publication. Old chunks are retained; there is no automatic garbage collection.

`KB_MANIFEST_PATH` defaults to `CHROMA_DB_PATH/kb_manifest.sqlite3`. Back up the Manifest, retained Chroma data, keyword index, and versioned image assets together. Rollback uses the existing published snapshot:

```python
from software_recommend_system.kb_versions import capture_snapshot, rollback_snapshot

previous = capture_snapshot()  # retain before publishing an update
# Run the existing ingestion/rechunk entrypoint to publish the update.
rollback_snapshot(previous)   # switches future readers; pinned tasks remain consistent
```

## Offline comparison and promotion

Run the provided recorded fixtures without calling any model:

```powershell
cd fastapi_demo
python -m evaluation.routing_evaluate --input evaluation/routing_cases.example.jsonl --output .runtime/routing-example
```

The example report must remain `HOLD`: it is a contract demonstration, not deployment evidence. For historical comparison, export frozen request-time context and independent human-reviewed intended targets using the same JSONL schema. Existing written session IDs are not ground truth. Keep conversation groups together, place calibration before holdout in time, exclude future context, and separate representative samples from challenge cases. Recorded predictions are the default; `--candidate jev --baseline legacy` explicitly performs paid decision-only calls. Neither evaluation nor shadow runs candidate graph nodes, retrieval, or external tools.

Review wrong existing-session assignment, eventual automatic coverage, Jev direct acceptance, review frequency, technical fallback, clarification, P50/P95/P99 latency, and actual usage/cost. Unlabeled decisions are not counted as correct. Disagreement with baseline is not an error label. RAG objective coverage is a separate metric. Decision-only replay cannot establish end-to-end execution latency or cost; use request-correlated runtime traces for that comparison.

The promotion gate evaluates wrong assignment **conditional on semantic auto-acceptance into an existing session**, including reviewer-accepted decisions and excluding explicit-ID requests. It uses one preselected representative holdout case per group and a one-sided 95% binomial upper bound <=0.001. With zero errors this requires at least 2,995 eligible independent groups. The gate also checks explicit-target invariants and the model/taxonomy/prompt/threshold policy fingerprint. Model or policy changes require new evidence. Report loading recomputes the gate rather than trusting a `passed` flag.

After a passing historical holdout, run shadow on live traffic and review labeled outcomes. Shadow uses a bounded queue and its own provider/circuit; queue saturation drops observations without changing live output. Set the same `ROUTING_GATE_REPORT`, deployment, and traffic configuration in Spring and FastAPI. Spring derives a stable, server-owned cohort key after ownership checks; both services use the same SHA-256 bucket.

Copy the repository's `routing.env.example` to `.runtime/routing.env` for shared controls in `start-all.ps1` and Docker Compose. Keep the provider key in `fastapi_demo/.env`. Local launch needs an absolute report path; Compose mounts `.runtime/routing/` at `/data/routing` in both services. Compose keeps source documents read-only and stores immutable image assets in the writable `fastapi_versioned_assets` volume mounted under `.versions`.

Promote through 1%, 5%, 25%, and 100%, with at least 24 hours of reviewed traffic at each stage. Before each increase, verify the session-risk bound, invariant violations, fallback/clarification rates, P95/P99 latency, cost, pending-task behavior, and snapshot consistency. This is an operator-controlled rollout; changing a percentage does not automatically certify the preceding observation window. Roll back to shadow on a wrong-session incident or a breached gate. Unknown token usage/pricing stays unknown rather than zero.

## Verification

Python tests cover classifier thresholds/failures, shadow isolation, risk-gate statistics, actual LangGraph interruption/resume, bounded active time, query protection/recall, and Manifest publication/pinning. Java tests cover preflight ownership/persistence ordering and task states; React tests cover attribution, waiting tasks, resume/cancel, and multimodal messages. Offline tests use fixtures and blocked external network access; they do not measure real Jev quality or throughput.
