# Ingestion Workflow Evaluation

Date: 2026-06-05

## Executive Summary

The ingestion workflow is working for the current small local test set. Batch upload, extraction, indexing, expected validation failures, retry, queue visibility, and final document states were all exercised through the running Docker Compose stack.

The current system is ready for small PDF ingestion and manual validation. It is not yet proven for larger-scale ingestion such as 25, 50, or 100 PDFs in one run. The biggest remaining weaknesses are observability and run lifecycle accuracy, not the basic document pipeline.

Main strengths:

- Valid PDFs move to `ready`.
- Page-limit failures and scanned/image-only PDFs are rejected with clear document-level messages.
- Extraction and indexing are handled asynchronously by separate RQ queues.
- Current queue depth was visible and drained to zero after the batch.
- Embedding calls are batched through `EMBEDDING_BATCH_SIZE`.
- Timeout/failure callback handling now exists so killed indexing jobs should not leave documents stuck forever.

Main weaknesses:

- Historical ingestion runs can remain `processing` or `preparing` even after their documents are no longer active.
- Expected business-rule rejections still appear in RQ failed registries, which makes operational failure counts noisy.
- There is no durable per-stage timing for queue wait, extraction time, embedding time, and DB write time.
- Large-batch behavior was not tested in this pass.
- Token-budget exhaustion behavior during concurrent ingestion was not tested in this pass.

## Test Setup

Environment:

- Docker Compose local stack on Windows.
- Client: `http://localhost:5173`.
- API: `http://localhost:8000`.
- RQ dashboard: `http://localhost:9181`.
- Postgres with pgvector: `rag-postgres`.
- Redis: `rag-redis`.
- Extraction workers: 5 `worker-extract` containers.
- Index workers: 3 `worker-index` containers.
- OpenAI calls: real embedding calls, not mocked.
- Supabase: used for auth and raw PDF storage.

Current configured limits observed from docs/config:

- Maximum PDF page count: `10`.
- Maximum file size: `10 MB`.
- Maximum backend batch upload size: `50`.
- Embedding batch size: `32`.
- Extract job timeout: `900` seconds.
- Index job timeout: `1800` seconds.
- OpenAI embedding request timeout: `300` seconds.

Documents tested in the latest real run:

| File | Expected Result | Actual Result |
| --- | --- | --- |
| `terms-and-conditions-template.pdf` | valid, under 10 pages | `ready`, 2 pages |
| `About_ChatGPT_Pro_tiers_OpenAI_Help_Center.pdf` | valid, under 10 pages | `ready`, 4 pages |
| `Terms-and-Conditions-Free-Template_PDF-2.pdf` | too many pages | `failed`, page-limit failure |
| `Screenshot_22_.pdf` | image-only/scanned | `failed`, unsupported-content failure |

## Workflow Validation

### Upload Preparation

Batch upload preparation worked. The frontend sent `POST /documents/upload-prepare-batch` and the server returned `201 Created`. The batch run was created in `ingestion_runs`.

The frontend/backend status mismatch found earlier was fixed: the backend returns prepared batch items, and the frontend now treats prepared items as uploadable instead of leaving documents at `pending_upload`.

### Upload Completion

Upload completion worked for the latest real run. Documents moved past `pending_upload`; no document remained stuck in upload state after the validated batch.

### Extraction

Extraction behaved correctly:

- Two valid PDFs produced page rows.
- One 28-page PDF failed because it exceeded the 10-page limit.
- One scanned/image-only PDF failed because no extractable text was found.
- The latest database state had 6 extracted `document_pages` rows, matching the two valid PDFs.

### Indexing

Indexing behaved correctly after the timeout/failure handling work and retry:

- The two valid PDFs became `ready`.
- The latest database state had 6 `chunks` rows and 6 `chunk_embeddings` rows.
- Index workers logged successful index jobs for the two valid documents.
- No latest document remained stuck in `indexing`.

### Final Document State

Latest document summary from Postgres:

| Metric | Value |
| --- | ---: |
| Total latest documents | 4 |
| Ready | 2 |
| Failed | 2 |
| In progress | 0 |
| Extracted pages | 6 |
| Chunks | 6 |
| Embeddings | 6 |
| Extract queue depth after run | 0 |
| Index queue depth after run | 0 |

The latest document-level states are correct.

### Retry and Recovery

Retry/recovery was exercised after a previous indexing worker timeout left a document in `indexing`. The stuck document was marked failed and retried, then reached `ready`.

The code now also wires explicit RQ job timeouts and an `on_failure` callback that marks active documents failed when an RQ job is killed or times out. A forced worker-kill test after this callback was added was not performed in this pass.

### Reindex

The reindex endpoint exists and the indexing code rebuilds chunks/embeddings idempotently. A full user-facing reindex scenario was not executed in this evaluation pass.

## Performance Findings

This was a small functional/performance check, not a high-scale benchmark.

Measured latest run:

| File | Final Status | Time From Row Creation To Final State |
| --- | --- | ---: |
| `Screenshot_22_.pdf` | failed | 7.88 seconds |
| `Terms-and-Conditions-Free-Template_PDF-2.pdf` | failed | 8.17 seconds |
| `terms-and-conditions-template.pdf` | ready | 13.88 seconds |
| `About_ChatGPT_Pro_tiers_OpenAI_Help_Center.pdf` | ready | 14.25 seconds |

Observed worker timing from logs:

- Valid extraction jobs started around `19:29:00` to `19:29:01` UTC.
- Valid extraction jobs finished around `19:29:04` to `19:29:05` UTC.
- Index jobs started around `19:29:04` to `19:29:05` UTC.
- Index jobs finished around `19:29:10` UTC.
- Queue wait was small for this run because workers were already idle.

Current bottleneck for small valid PDFs appears to be indexing/embedding, not extraction. That conclusion is limited because only two valid PDFs were indexed in the latest run.

Not measured:

- 10, 25, 50, or 100 PDF batch throughput.
- Average queue wait under sustained backlog.
- Worker utilization.
- DB query/write count.
- OpenAI request latency by batch.
- Throughput while queries are being served at the same time.

## Reliability Findings

What passed:

- Valid documents reached `ready`.
- Invalid documents reached `failed`.
- Failure messages were specific and visible in the UI.
- Expected failures were isolated to the individual documents.
- Queues drained to zero after the run.
- The latest run had no documents stuck in `pending_upload`, `uploaded`, `extracting`, or `indexing`.

Issues found:

- Earlier, an index worker was killed after the default RQ timeout and the document remained `indexing`. This exposed a real failure-state bug. The code now has explicit job timeouts and a failure callback, but a deliberate post-fix kill test is still needed.
- RQ failed registries contain expected validation failures. At inspection time Redis had `11` failed extract jobs and `7` failed index jobs from current/prior runs. These counts are noisy because they mix expected document rejections with infrastructure failures.
- Historical ingestion runs are not always terminal in the database. At inspection time `ingestion_runs` had statuses: `partial = 1`, `processing = 5`, `preparing = 4`. Some of these are older runs from earlier failed attempts, but this shows run lifecycle cleanup/status derivation still needs hardening.

## Queue and Worker Behavior

Current worker topology is stronger than the original baseline:

- 5 extraction workers listen on `ingest_extract`.
- 3 indexing workers listen on `ingest_index`.
- Each document still uses one extract job and one index job.
- Valid documents can extract and index in parallel when workers are available.

Observed state after the latest run:

- `rq:queue:ingest_extract` length: `0`.
- `rq:queue:ingest_index` length: `0`.
- Workers remained up and continued cleaning registries.

Remaining gaps:

- There is no backpressure policy beyond queueing.
- There is no persistent queue-depth history.
- There is no clear separation between expected document rejection and worker/system failure in RQ failure monitoring.
- There is no automatic stale-run reconciliation job.

## Database Efficiency

Improvements now present:

- Page rows are inserted in a batch-style execution.
- Embedding vectors are inserted in batches.
- Indexing clears and rebuilds chunks/embeddings for idempotent retry/reindex behavior.

Remaining concerns:

- Work is still organized one document per extract job and one document per index job.
- There is no recorded DB write count per ingestion run.
- There is no stage timing stored alongside rows.
- There is no evidence yet for lock contention or write amplification under load because larger runs were not tested.

## Embedding Efficiency

The implementation now uses `EMBEDDING_BATCH_SIZE`, configured as `32`, so it no longer embeds one chunk per OpenAI call by design.

In the latest run there were only 6 total chunks, so each document fit within a small number of embedding calls. This validates correctness but does not prove high-throughput embedding behavior.

Embedding is still the most likely throughput bottleneck for larger text-heavy PDFs because it depends on external OpenAI latency, quota, and rate limits.

## Token-Budget Safety

What exists:

- Indexing reserves token budget before embedding batches.
- Successful embedding batches commit usage.
- Failure paths attempt to release outstanding reservations.
- A maintenance job exists for stale reservation cleanup.

What was not tested:

- Low-budget ingestion failure.
- Concurrent uploads competing for the same workspace budget.
- Reservation cleanup after forced worker termination.
- Stale reservation detection after long-running or killed jobs.

This area needs a dedicated failure test before the ingestion system can be considered robust under concurrency.

## Observability Findings

What is working:

- The UI shows ready, failed, and in-progress document groups.
- The API exposes `GET /documents/ingestion-queues`.
- The API exposes `GET /documents/ingestion-runs/{run_id}`.
- Document-level error messages are clear.
- Docker logs are useful for worker-level diagnosis.

What is weak:

- Run status history is not fully reliable for older runs.
- Expected validation failures create RQ failure noise.
- There is no per-stage timing model.
- There is no queue-depth trend, worker utilization view, or OpenAI latency breakdown.
- There is no simple operator command/API that says "show stuck ingestion work".

## Multi-Document Readiness

The ingestion side now supports multiple uploaded documents and preserves workspace/document metadata in pages, chunks, and embeddings.

This helps future multi-document retrieval because the data model already carries:

- `workspace_id`
- `document_id`
- page rows
- chunk rows
- embedding rows

The main remaining multi-document gap is on the query side, not ingestion. Retrieval and chat are still effectively scoped to one selected document at a time.

## What Is Missing

Important missing pieces:

- Larger load tests with 10, 25, and 50 small PDFs.
- Forced worker-kill test after the new failure callback.
- Low token-budget ingestion test.
- Reindex test from the UI/API.
- Automated run-status reconciliation.
- Cleaner RQ failure semantics for expected document rejection.
- Durable stage timing fields or metrics.
- Queue-depth metrics over time.
- Integration tests for batch upload, partial success, retry, and stuck-job recovery.

## Recommended Next Improvements

Highest impact for reliability:

1. Add a reconciliation job that marks stale `uploaded`, `extracting`, and `indexing` documents failed after a configured timeout.
2. Update ingestion-run status from document states consistently, or derive it dynamically instead of trusting stale stored statuses.
3. Separate expected validation failures from infrastructure failures so RQ failed registries are actionable.
4. Add an automated test for RQ worker timeout/kill recovery.

Highest impact for throughput:

1. Add per-stage timing metrics for upload completion, queue wait, extraction, indexing, embedding, and DB writes.
2. Run repeatable 10/25/50 PDF load tests with small text PDFs.
3. Record embedding batch counts and OpenAI latency per document.
4. Consider bulk DB insert optimization only after measuring real DB bottlenecks.

Highest impact for observability:

1. Add a `GET /documents/ingestion-health` endpoint with stuck counts, queue depths, failed registry counts, and oldest active document age.
2. Add run-level timing and final status summary fields.
3. Show infrastructure failures separately from validation failures in the UI.
4. Add an operator command or script to inspect stuck documents and stale reservations.

Future multi-document retrieval work:

1. Add selected-document or workspace-wide retrieval APIs.
2. Update retrieval filters to search across multiple document IDs.
3. Update the chat UI so a user can select multiple ready documents.
4. Preserve citation quality by returning document title, page, and chunk metadata for every source.

## Recommended Next Tasks

Concrete next task files to create:

1. `docs/task/ingestion-observability-and-stuck-job-recovery.md`
2. `docs/task/ingestion-load-test-suite.md`
3. `docs/task/token-budget-ingestion-failure-tests.md`
4. `docs/task/multi-document-retrieval-api.md`
5. `docs/task/multi-document-chat-ui.md`

## Final Assessment

Task 4 validates that the improved ingestion workflow works for the current real local test set. The latest batch ended in correct document states, queues drained, and valid PDFs became queryable.

The pipeline should not yet be called large-scale ready. The next engineering focus should be observability, stale-state recovery, and repeatable load testing before adding more ingestion features.
