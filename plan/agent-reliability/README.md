# Task Plan: Agent reliability and resume evidence

## Overall goal
Document the existing retrieval benchmark honestly, then make recommendation and confirmation runs time-bounded, cancellable, and visibly unsuccessful on failure.

## Subproblems
1. `01-evidence-and-story.md` - recalculate stored benchmark results and create interview-ready evidence - status: verified
2. `02-python-run-control.md` - add run deadlines, bounded execution, cancellation, node guards, and graph limit - status: verified
3. `03-stream-propagation.md` - propagate timeout, cancellation, and failure through FastAPI, Spring, and React - status: verified
4. `04-fault-verification.md` - add fault tests, run repository checks, and record measured results - status: verified with limits

## Dependencies
Step 1 is independent. Step 2 establishes the control interface used by step 3. Step 4 verifies steps 2 and 3 and updates the evidence story with actual results.

## Recommended execution order
1. Record raw benchmark provenance before changing runtime code.
2. Implement Python run control and its failure contract.
3. Carry cancellation and terminal state across Spring and React.
4. Run focused fault tests, the existing Python and Java suites, and the React checks; update status only from observed results.

## End-to-end verification
- Recompute the retrieval benchmark from the checked-in raw JSON and verify sample and eligibility counts.
- Deterministic tests cover deadline return, session contention, cooperative cancellation, stream completion, and stream cancellation. A frontend test covers SSE stop metadata.
- After rebasing onto the latest remote master, Python suite: 180 passed. Java Maven tests passed. Frontend Jest: 3 passed; production build passed. Focused Ruff checks passed.
- The checks do not exercise a live browser-to-Spring-to-FastAPI disconnect, LangGraph HITL pause/resume, recursion exhaustion, or downstream provider billing. Cancellation remains cooperative for an already-running synchronous call.
- See `story/2026-09-24-agent-reliability-evidence.md` for exact commands and limits.
