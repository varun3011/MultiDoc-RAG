# Report Task: Ingestion Workflow Evaluation

## Purpose

This file defines a follow-up reporting task for the ingestion improvement work described in [docs/task/ingestion-pipeline-scale-and-reliability.md](D:/Desktop/projects/MultiDoc-RAG/docs/task/ingestion-pipeline-scale-and-reliability.md:1).
This file defines a follow-up reporting task for the ingestion improvement work described in `docs/task/ingestion-pipeline-scale-and-reliability.md`.

The purpose of this task is not to add features directly. The purpose is to test, inspect, and evaluate the ingestion workflow properly after implementation work, then produce a grounded report that explains:

- what is working well
- what is still missing
- what is still unreliable
- what is still too slow
- which areas need more improvement
- what should be changed next

This report is performance-first and reliability-first.

## Why This Report Is Needed

The ingestion pipeline task is broad and likely to be implemented in phases. After changes are made, it will be easy to assume the system is “good enough” without properly testing:

- high document counts
- queue pressure
- worker failures
- retry behavior
- token-budget edge cases
- DB write amplification
- partial success scenarios

This report task exists so an agent can evaluate the real behavior of the backend and produce a structured assessment instead of relying on assumptions.

## What This Report Should Evaluate

The report should cover the ingestion workflow end to end:

1. upload preparation
2. upload completion
3. queue enqueue behavior
4. extraction worker behavior
5. indexing worker behavior
6. embedding throughput
7. DB write patterns
8. token-budget behavior during ingestion
9. retry and reindex behavior
10. failure recovery
11. observability
12. readiness for future multi-document retrieval

## Current Baseline To Compare Against

The current codebase baseline is:

- one-file-at-a-time upload prepare
- one-file-at-a-time upload complete
- one extract job per document
- one index job per document
- chunk insertion one row at a time
- embedding generation one chunk at a time
- vector insertion one row at a time
- query flow still scoped to one document at a time

Primary reference files:

- `docs/project-context.md`
- `docs/task/ingestion-pipeline-scale-and-reliability.md`
- `server/app/api/documents.py`
- `worker/jobs/ingest_extract.py`
- `worker/jobs/ingest_index.py`

## Main Evaluation Questions

The report should answer these questions clearly.

### 1. Throughput

- Can the backend ingest `N` small PDFs without obvious bottlenecks?
- At what point does throughput degrade noticeably?
- Which stage is the bottleneck:
  - upload orchestration
  - extraction
  - indexing
  - embeddings
  - database writes
  - queue waiting

### 2. Reliability

- Do documents consistently reach a correct final state?
- Are failures isolated cleanly to individual documents or runs?
- Are retries safe and idempotent?
- Are partial failures recoverable without manual cleanup?

### 3. Queue and Worker Behavior

- Are extract and index queues balanced well?
- Do workers sit idle in one stage while another stage backs up?
- Is there backpressure behavior when the queue grows large?
- Do worker crashes leave jobs, documents, or reservations in bad states?

### 4. Database Efficiency

- Are inserts and updates still too chatty?
- Are there unnecessary transactions?
- Is indexing doing avoidable row-by-row work?
- Are there signs of lock contention or high write amplification?

### 5. Embedding Efficiency

- Are embeddings still created in too many small calls?
- Is batching being used effectively if implemented?
- Is embedding throughput the dominant bottleneck?
- Does usage accounting around embeddings create extra overhead?

### 6. Token-Budget Safety

- Does ingestion stop safely when budget is insufficient?
- Are reservations released correctly on failure paths?
- Are there stale or stranded reservations?
- Is the budget model predictable during concurrent ingestion?

### 7. Observability

- Can an operator understand where time is being spent?
- Are ingestion failures visible enough?
- Is there enough information to diagnose queue delay vs processing delay?
- Is there enough run-level or batch-level visibility if bulk ingestion was added?

### 8. Multi-Document Readiness

- Did ingestion improvements preserve clean metadata for future multi-document retrieval?
- Is the storage and chunk model still easy to extend for cross-document query?
- Did any optimization choices make future multi-document retrieval harder?

## Expected Report Structure

When this task is executed, the generated report should contain sections like these:

### 1. Executive Summary

- short summary of system health
- biggest strengths
- biggest weaknesses
- whether the ingestion pipeline is ready for larger-scale use

### 2. Test Setup

- environment used
- worker counts
- DB and Redis setup
- document count used in tests
- document size/page profile
- whether OpenAI calls were real or mocked

### 3. Workflow Validation

Explain whether each stage behaves correctly:

- upload prepare
- upload complete
- extraction
- indexing
- final document state
- retry
- reindex

### 4. Performance Findings

Include:

- throughput numbers
- average per-document time
- queue wait time
- stage-by-stage bottlenecks
- comparison against previous baseline if available

### 5. Reliability Findings

Include:

- failure modes found
- inconsistent state transitions
- stuck jobs
- stranded reservations
- duplicate processing risk
- retry safety issues

### 6. What Is Missing

List the important gaps still remaining after the ingestion task implementation.

Examples:

- no strong batch run visibility
- no queue depth reporting
- indexing still too sequential
- weak retry semantics
- insufficient metrics

### 7. What Needs Improvement Next

This section should be actionable.

It should rank improvements by impact:

1. highest impact for throughput
2. highest impact for reliability
3. operational/observability fixes
4. design work that helps future multi-document retrieval

### 8. Recommended Next Tasks

Each recommended next task should be concrete and narrowly scoped.

Examples:

- batch embedding writes
- ingestion run tracking
- queue depth metrics endpoint
- worker crash recovery hardening
- token-budget cleanup improvements

## Suggested Test Scenarios

The agent running this report task should try to cover the following.

### Load Scenarios

- 10 small PDFs
- 25 small PDFs
- 50 small PDFs
- 100 small PDFs
- larger counts if practical

### Failure Scenarios

- one corrupted PDF in the middle of a batch
- storage verification failure
- worker interruption during extraction
- worker interruption during indexing
- low token-budget case
- retry after failure
- reindex after successful ingestion

### Concurrency Scenarios

- many documents uploaded together
- extraction backlog greater than indexing backlog
- indexing backlog greater than extraction backlog
- ingestion running while queries are also being served

## Metrics To Capture

At minimum, the report should try to capture:

- total documents ingested
- total time for full ingestion run
- average extraction time per document
- average indexing time per document
- average queue wait time
- documents failed
- documents retried successfully
- token-budget failures
- stuck or long-running jobs

If deeper instrumentation exists, also capture:

- DB write counts or query counts
- embedding batch size
- queue depth over time
- worker utilization

## Red Flags To Watch For

The report should explicitly call out these kinds of problems if found:

- documents stuck in `uploaded`, `extracting`, or `indexing`
- duplicate chunk or embedding generation
- retries causing inconsistent state
- stale token reservations
- queue starvation between extract and index phases
- ingestion speed collapsing sharply with moderate load
- too much dependence on manual operator cleanup
- improvements that help ingestion but block future multi-document retrieval

## Output Requirement

The final report produced by this task should be written in plain, concrete terms. It should not be a vague summary. It should identify:

- what was tested
- what passed
- what failed
- where time is going
- what is still weak
- what should be done next

## Summary

This task exists to turn ingestion work into measurable engineering feedback. The earlier task improves the pipeline. This report task verifies whether that work actually solved the performance and reliability problems, identifies what is still weak, and creates a grounded path for the next round of backend improvements.
