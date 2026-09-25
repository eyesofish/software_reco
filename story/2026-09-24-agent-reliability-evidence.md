# Software Reco: evidence snapshot and interview story

## Verified repository evidence

| Claim | Evidence | Status |
|---|---|---|
| Hybrid retrieval plus reranking raised equal-bucket Recall@10 from 0.7775 to 0.8428 (+6.54 percentage points). | Three stored buckets for `nvidia/TechQA-RAG-Eval` test; 300 rows per method and 222 eligible rows per method. Buckets contain 69, 74, and 79 eligible rows; 78 rows were excluded because gold document IDs were missing. | Historical measured result; arithmetic independently recomputed from the checked-in per-example JSON. |
| Mean Hit@10 improved from 0.7775 to 0.8428. | The checked-in retrieval report records the same metric as Recall@10 because this evaluation uses binary relevance. | Historical measured result. |
| The end-to-end evaluation achieved routing accuracy 1.0 across each of three 100-example runs; judge-based answer correctness ranged from 0.5399 to 0.6020 and p50 latency from 13.04 to 13.95 seconds. | `e2e_real_eval_3x100_summary_20260312_224001.md` summarizes the three reports. | Historical measurements; report the range and benchmark context. |
| Recommendation execution uses bounded workers, deadline/cancellation signals, graph recursion limits, and explicit failed/incomplete stream states. | `fastapi_demo/software_recommend_system/execution_control.py`, Spring cancellable SSE bridge, React incomplete-state handling, and deterministic tests below. | Implemented; component tests pass. Full cross-service disconnect behavior is not integration-tested. |

## Benchmark scope and limitation

The retrieval benchmark uses 100 examples in each of three test buckets. Only 69, 74, and 79 examples have gold document IDs, for 222 eligible rows and 78 exclusions per method. Web and memory recall are disabled. The method comparison uses the same parent-child index. The headline values are the report's unweighted arithmetic mean of the three bucket metrics. This is an offline benchmark, not production traffic or an A/B test.

## Backend resume draft

> Built a cross-layer recommendation service with FastAPI, LangGraph, Spring Boot, and SSE; added bounded graph execution, request deadlines, disconnect-triggered cancellation, and explicit incomplete-stream states. Verified deadline return, cooperative cancellation, session contention, and SSE failure metadata with deterministic tests; an already-running synchronous provider call exits through its own timeout rather than forcible thread termination.

## AI application resume draft

> Built and evaluated a LangGraph RAG recommendation pipeline; on 222 eligible TechQA test examples across three buckets, reranking improved mean Recall@10 from 77.75% to 84.28% (+6.54 percentage points). Separately measured answer correctness of 53.99%–60.20% in three 100-question end-to-end runs, identifying answer quality as a remaining limitation.

## Chinese interview summary

项目有两条可以拿数据说明的主线。检索实验在同一父子索引和关闭 Web、记忆通道的条件下比较不同方法；在 222 条带标注样本上，加入重排后，三组 Recall@10 的等权平均从 77.75% 提升到 84.28%。端到端评测也显示了局限：路由准确率为 100%，但三次评测的答案正确性为 53.99% 到 60.20%，因此不能仅凭检索提升宣称最终回答已经可靠。可靠性实现增加了执行 deadline、会话并发保护、协作取消、SSE 终止元数据和未完成状态；基于更新后的远端 master，Python 180 项、Java 测试、前端 3 项测试和前端构建均通过。跨服务真实断连和模型供应商侧停止计费尚未由集成测试证明。

## Likely interview follow-ups

| Question | Evidence-based answer |
|---|---|
| How do you know reranking helped? | The stored baseline suite covers three buckets, uses the same parent-child index and disabled web/memory retrieval, and reports Recall@10 rising from an equal-bucket mean of 0.7775 to 0.8428. |
| Was that 300 independent labeled examples per method? | There are 300 rows, but only 222 are eligible because the remaining 78 have no gold document IDs. |
| Did final-answer quality improve by the same amount? | No. This retrieval ablation measures document retrieval. Separate judge-based end-to-end runs report answer correctness between 0.5399 and 0.6020; those results show further work is needed. |
| Is the reliability work already proven? | State only the specific changes and fault tests that have passed. Do not claim hard thread termination or stopped provider billing; a synchronous network call exits through its own timeout or explicit cancellation support. |

## Reliability verification

- `cd fastapi_demo; python -m pytest tests -q`: 180 passed on the rebased branch. A 30 ms deadline returns within the test's 200 ms bound while a simulated synchronous worker is still finishing; the worker then releases its session. Tests also cover same-session conflict, caller cancellation, cooperative stream cancellation, and normal stream completion.
- `cd fastapi_demo; python -m ruff check software_recommend_system/execution_control.py tests/test_execution_control.py --output-format concise`: passed.
- `cd ollama_springboot/demo; .\\mvnw.cmd -q test`: passed on JDK 24 with explicit Lombok annotation processing.
- `cd ollama_springboot/ollama-gui-reactjs; npm test -- --watchAll=false --runInBand`: 3 passed, including SSE stop reason/run id/elapsed time normalization.
- `cd ollama_springboot/ollama-gui-reactjs; npm run build`: passed.
- The full Python run emitted a LangSmith API 403 during tracing upload although all tests passed. It did not affect local test results.
- These are component-level checks. No real cross-service disconnect test measured late downstream calls. Python cannot forcibly terminate an in-flight synchronous thread; client-side timeouts bound supported outbound calls, and cancellation checks stop subsequent graph work after control returns.
