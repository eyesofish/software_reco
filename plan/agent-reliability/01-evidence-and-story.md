# Subproblem 1: Record benchmark and project evidence

## 1. Goal
Create a verifiable snapshot of the retrieval benchmark and interview material grounded in repository evidence.

## 2. Why this step exists
The resume goal requires evidence that a reviewer can inspect; historical benchmark improvements must not be attributed to the upcoming reliability change.

## 3. Files involved
- `fastapi_demo/evaluation/reports/retrieval_benchmark_suite_20260313_113353/raw/` - checked-in benchmark JSON is the source of truth.
- `fastapi_demo/evaluation/reports/retrieval_baseline_summary_20260313_113353.md` - documents benchmark setup and aggregate results.
- `story/` - destination for source, sample counts, arithmetic, resume wording, and interview notes.

## 4. Exact changes
- Recompute per-method equal-bucket means from raw JSON without invoking retrieval or a language model.
- Record 300 total examples across three buckets, 222 eligible examples per method, and the 78 excluded rows and reason.
- Explain the fixed parent-child index and disabled web and memory channels; distinguish historical measurements from work completed in this task.
- Draft separate backend and AI-application resume bullets; mark the reliability bullet as a template until its checks pass.

## 5. Out of scope
Do not rerun paid model evaluation, modify retrieval settings, or claim a production impact.

## 6. Done condition
Every numeric claim can be recalculated from a checked-in raw file, and no planned result is described as completed.

## 7. Verification
Run a local script over the stored JSON and check its counts and means against the written snapshot.

## 8. Expected output
An evidence and interview-story markdown file under `story/`.

## 9. Notes for the next step
The historical retrieval result can be presented independently of the reliability implementation.

## 10. Risks or ambiguity
Means across three buckets are not weighted by eligible sample count; label this as the report's equal-bucket mean.
