# Task: Ingestion Follow-Up Hardening and Validation

## Status

Derived from the current ingestion workflow audit and the existing evaluation report.

## Goal

Capture the important ingestion gaps that are still not implemented or still weak, then define:

1. how each gap should be implemented
2. what should be tested after implementation

This file is meant to turn the audit into concrete follow-up work.

## Why This Task Exists

The repository already has a working ingestion pipeline for small PDFs, including:

- batch upload prepare and complete
- ingestion runs
- extraction and indexing workers
- structured failure messages
- retry and reindex flows
- queue visibility
- token-budget reservation and cleanup

But the system is still missing several important pieces before the ingestion path can be considered robust, measurable, and ready for higher-volume usage.

## Scope

This task covers the remaining weak points found in the audit:

1. no repeatable load and throughput validation
2. weak ingestion observability
3. incomplete stale-state and run lifecycle recovery
4. schema and runtime status mismatch
5. query path still single-document
6. thin automated ingestion test coverage

## Recommended Order

1. Fix schema and status-model mismatch
2. Add stale document and run reconciliation
3. Add ingestion observability and operator health visibility
4. Add automated ingestion integration tests
5. Add repeatable load-test tooling
6. Extend retrieval and querying toward multi-document support

The first three items are backend safety and operability work. They should happen before calling the ingestion system stable at larger scale.

## Follow-Up Items

### 1. Repeatable Load and Throughput Validation Is Missing

#### Current Weakness

The code supports batch ingestion, but there is no repeatable load-test harness for:

- 10 PDFs
- 25 PDFs
- 50 PDFs
- 100 PDFs

There is also no durable way to compare one ingestion revision against another.

#### How To Implement

- Add a dedicated load-test task doc and script set under `scripts/` for ingestion benchmarking.
- Create a repeatable test dataset of small text PDFs with known page counts and known expected outcomes.
- Add a driver that can:
  - create or reuse a workspace
  - prepare and complete uploads in bulk
  - poll ingestion runs until terminal
  - capture final document states
  - measure total wall-clock run time
  - measure per-document completion time
- Persist results to a simple JSON or markdown artifact so runs can be compared over time.
- Include scenarios with:
  - all-valid batches
  - mixed valid and invalid documents
  - queue backlog conditions

#### What To Test After Implementation

- 10 valid small PDFs complete successfully.
- 25 valid small PDFs complete successfully.
- 50 valid small PDFs complete successfully.
- Mixed valid and invalid batches still complete with isolated failures.
- The load-test artifact records:
  - total duration
  - per-document duration
  - success count
  - failure count
  - queue depth snapshots if available
- Re-running the same scenario produces comparable output structure.

### 2. Ingestion Observability Is Still Weak

#### Current Weakness

The system exposes queue counts and document/run status, but it still lacks:

- stage timing
- queue-depth history
- worker utilization visibility
- operator-facing ingestion health summary
- clear separation between expected document rejection and infrastructure failure

#### How To Implement

- Add stage timestamps or timing fields for key transitions:
  - upload completed
  - extract job enqueued
  - extraction started
  - extraction finished
  - index job enqueued
  - indexing started
  - indexing finished
- Add an operator endpoint such as `GET /documents/ingestion-health` that returns:
  - current queue counts
  - failed registry counts
  - oldest active document age
  - stale active document count
  - active ingestion run count
  - stale reservation count if practical
- Add structured worker logs for:
  - queue wait start/end
  - extraction duration
  - indexing duration
  - embedding batch counts
  - embedding API latency if practical
- Track expected validation failures separately from infrastructure failures in logs or API summaries.

#### What To Test After Implementation

- A successful document exposes complete stage timing.
- A failed validation document is visible as a document failure but not mislabeled as infrastructure failure.
- The health endpoint reports queue and stale-state summaries correctly during active ingestion.
- Worker logs contain enough information to distinguish:
  - queue delay
  - extraction time
  - indexing time
  - embedding time
- The UI or operator flow can identify whether a slowdown is caused by queue wait or processing.

### 3. Stale-State and Run Lifecycle Recovery Is Incomplete

#### Current Weakness

There is failure callback coverage for killed jobs, but there is still no general reconciliation process for:

- documents stuck in `uploaded`
- documents stuck in `extracting`
- documents stuck in `indexing`
- ingestion runs left in `preparing` or `processing` after work is effectively done

#### How To Implement

- Add a scheduled reconciliation job for ingestion state.
- Define configurable stale thresholds for:
  - `uploaded`
  - `extracting`
  - `indexing`
- Reconciliation should:
  - mark stale active documents as `failed`
  - attach a structured transient-infrastructure error message
  - refresh or recompute affected ingestion run statuses
- Consider deriving ingestion run status dynamically from current document states instead of trusting persisted run status alone.
- Ensure reconciliation is idempotent and safe to run frequently.

#### What To Test After Implementation

- A document intentionally left in `uploaded` beyond threshold is marked `failed`.
- A document intentionally left in `extracting` beyond threshold is marked `failed`.
- A document intentionally left in `indexing` beyond threshold is marked `failed`.
- A run with only terminal document states is reconciled to `completed`, `partial`, or `failed` correctly.
- Re-running reconciliation does not corrupt already-terminal rows.
- Retry still works correctly after reconciliation marks a document failed.

### 4. Schema and Runtime Status Model Do Not Fully Match

#### Current Weakness

The code uses a richer runtime model including statuses like:

- `uploading`
- `extracting`
- `indexed`

But the SQL schema files still define a narrower `documents.status` check constraint. The code currently works around this mismatch defensively, which is fragile.

#### How To Implement

- Update `scripts/schema.local.sql` and `scripts/schema.supabase.sql` so the document status constraint matches the actual runtime statuses used by the application and workers.
- Review migrations or schema upgrade steps so existing environments can be brought to the same status model safely.
- Remove compatibility branches only after the schema is consistently aligned across environments.
- Ensure frontend status types, backend schemas, workers, and DB constraints all use the same status vocabulary.

#### What To Test After Implementation

- Fresh schema setup accepts every runtime status the code uses.
- Existing environments can migrate without data loss.
- Worker transitions no longer rely on fallback behavior for missing statuses.
- Upload, extract, index, retry, and reindex transitions all succeed against the aligned schema.
- Status values shown in the UI match the persisted DB states exactly.

### 5. Retrieval and Querying Are Still Single-Document

#### Current Weakness

The ingestion side now supports many documents per workspace, but the query side still accepts one `document_id` and retrieves chunks from only one document at a time.

This is not an ingestion breakage, but it is a real readiness gap for the intended multi-document system.

#### How To Implement

- Add a new query contract that accepts multiple selected document IDs.
- Enforce explicit query-side limits for:
  - max selected documents
  - max total pages across selected documents
- Update retrieval SQL to search across a selected document set instead of one document.
- Preserve citation quality by returning document metadata together with page and chunk references.
- Update query logs and observability so they record all searched documents, not one.

#### What To Test After Implementation

- A query across two ready documents returns citations from both when relevant.
- Query limits reject oversized document selections safely.
- Workspace isolation still holds for multi-document queries.
- Query logs record the selected document set accurately.
- Existing single-document flows continue to work.

### 6. Automated Ingestion Test Coverage Is Too Thin

#### Current Weakness

The repository has helper-level tests for ingestion policy, ingestion run status derivation, and token-budget primitives, but it does not have strong automated coverage for:

- batch upload orchestration
- partial success batches
- retry flows
- reindex flows
- stuck-job recovery
- low-budget ingestion failure
- queue failure callback behavior

#### How To Implement

- Add integration-style tests around the documents API and worker jobs.
- Use fixtures or mocks for:
  - Supabase storage interactions
  - OpenAI embeddings
  - Redis/RQ enqueue behavior where full worker execution is not needed
- Add job-level tests for:
  - extraction success
  - page-limit failure
  - unsupported-content failure
  - indexing success
  - budget failure
  - cleanup after failure
- Add API tests for:
  - batch prepare
  - batch complete
  - retry allowed vs retry denied
  - reindex with and without existing pages

#### What To Test After Implementation

- Batch prepare returns mixed accepted and rejected results correctly.
- Batch complete enqueues extraction correctly and fails safely on missing objects.
- Retry only works for retryable failures.
- Reindex chooses extract or index path correctly based on existing page rows.
- Index budget exhaustion leaves no stranded reservations.
- Failure callback marks timed-out or killed jobs as failed.
- Cleanup removes stale chunks, embeddings, and pages when expected.

## Acceptance Criteria

This task should be considered complete only when:

- the remaining ingestion weak points are each turned into explicit implementation work
- every item has a clear post-implementation validation plan
- the schema matches the runtime status model
- stale-state recovery exists for documents and runs
- ingestion observability is materially better
- automated ingestion coverage is broader than helper-only unit tests
- repeatable load validation exists for larger batches

## Summary

The ingestion pipeline is no longer missing its basic architecture. The important remaining work is hardening, observability, lifecycle recovery, automated validation, and true multi-document readiness.

This task captures that follow-up work in a way that is concrete enough to implement and test.
