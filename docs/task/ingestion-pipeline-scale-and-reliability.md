# Task: Ingestion Pipeline Scale and Reliability

## Status

Drafted from current codebase and confirmed product direction.

## Goal

Enhance the ingestion pipeline end to end so the backend can ingest large numbers of small PDFs reliably and with better throughput.

Primary focus:

1. Performance
2. Reliability
3. User experience later, mainly through frontend follow-up tasks

This task is backend-first, but it must be designed so future multi-document querying fits naturally on top of the ingestion and retrieval model.

## Scope Confirmed

- Focus on backend only for this task
- Target documents are small PDFs
- Expected per-document limits:
  - up to 10 pages
  - up to 10 MB
- Multi-document query is not the main deliverable of this task, but the design must prepare for it
- Frontend improvements can come later in separate tasks

## Why This Task Exists

The current project already supports:

- workspace-scoped uploads
- asynchronous extraction and indexing
- token-budget tracking
- grounded query and streaming query
- retry and reindex flows

But the current ingestion path is still shaped around single-document operations and moderate load. If the system needs to ingest `N` documents smoothly and predictably, the backend needs a stronger bulk-ingestion design, better throughput, clearer failure handling, and better operational visibility.

## Current Workflow

### Upload and Ingestion Today

1. The client calls `POST /documents/upload-prepare` for one file.
2. The API creates one placeholder document row and returns one signed upload URL.
3. The client uploads one PDF directly to Supabase Storage.
4. The client calls `POST /documents/upload-complete` for that one file.
5. The API verifies storage state and enqueues one extraction job.
6. `ingest_extract` downloads the PDF, extracts text, stores page rows, and enqueues indexing.
7. `ingest_index` reads `document_pages`, chunks text page by page, creates embeddings, writes chunk rows and vector rows, then marks the document ready.

Primary files:

- `server/app/api/documents.py`
- `worker/jobs/ingest_extract.py`
- `worker/jobs/ingest_index.py`
- `worker/worker.py`

### Querying Today

1. The client selects one active document.
2. The client sends one `document_id` and one question.
3. The API embeds the question.
4. Retrieval searches chunks for that one document only.
5. The API calls the LLM with grounded context and returns citations.

Primary files:

- `server/app/schemas/query.py`
- `server/app/api/query.py`
- `server/app/core/retrieval.py`

## What Is Missing Today

### 1. No bulk ingestion contract

The backend currently works one document at a time:

- one `upload-prepare` request per file
- one `upload-complete` request per file
- one extract job per file
- one index job per file

This is workable for small manual usage, but not ideal for ingesting large sets of documents efficiently.

### 2. Throughput is limited by per-document overhead

The current flow repeats a lot of work per file:

- repeated API validation
- repeated enqueue calls
- repeated storage verification
- repeated status transitions

This overhead becomes expensive when `N` grows.

### 3. Indexing is still very sequential

The current indexing job does a lot of row-by-row and chunk-by-chunk work:

- chunk rows are inserted one at a time
- embeddings are created one chunk at a time
- embedding usage is reserved and committed one chunk at a time
- vector rows are inserted one at a time

This hurts throughput and increases DB and API round-trips.

### 4. Failure handling is document-level, not ingestion-run-level

The system can mark a document failed and retry it, which is good. But it does not yet have a strong concept of a large ingestion run:

- no batch identity
- no run summary
- no grouped progress reporting
- no aggregated failure view
- no clear backpressure behavior when the queue grows

### 5. Observability is query-heavy, not ingestion-heavy

The project has useful usage and query observability, but ingestion-specific visibility is weaker:

- no stage-level throughput metrics
- no queue-depth reporting in the API
- no ingestion latency histograms by stage
- no batch-level progress state

### 6. Rate limits are tuned for ordinary usage, not bulk ingestion

Current defaults:

- `upload-prepare`: 10 per minute
- `upload-complete`: 20 per minute

These values are too restrictive for large ingestion workflows unless a more deliberate bulk ingestion design is introduced.

### 7. Retrieval is not yet multi-document

This is not strictly an ingestion problem, but it matters for design:

- the query schema accepts one `document_id`
- retrieval filters to one document
- the frontend centers around one active document

Any ingestion improvements should preserve or improve the metadata model needed for future retrieval across many documents in one workspace.

## Desired End State

After this task is implemented, the backend should:

- ingest many small PDFs more efficiently
- handle queue load more predictably
- reduce unnecessary per-document overhead
- make failures easier to understand and recover from
- expose better ingestion observability
- preserve workspace isolation and token-budget safety
- prepare the data and API design for future multi-document retrieval

## Non-Goals

These are not the main goals of this task:

- frontend bulk-upload UX
- OCR support
- support for larger PDFs beyond current constraints
- replacing pgvector or changing core storage providers
- full multi-document chat UX

## Implementation Direction

The agent implementing this task should evaluate and likely combine the following improvements.

### A. Add a bulk-ingestion backend contract

Introduce a backend path that supports many documents as one logical ingestion operation.

Possible directions:

- batch upload-prepare endpoint
- batch upload-complete endpoint
- ingestion-run record or batch record
- per-run progress and error summary

The exact shape can vary, but the system should stop treating high-volume ingestion as unrelated single-file actions.

### B. Reduce enqueue and orchestration overhead

The orchestration layer should be more efficient under load.

Possible directions:

- batch enqueueing for extraction/index jobs
- explicit job metadata for grouping and tracing
- clearer separation between user-facing completion and background scheduling
- queue policies for fair processing and backpressure

### C. Improve extract and index worker throughput

The workers should be reviewed for throughput bottlenecks.

Likely improvements:

- more efficient DB writes for `document_pages`
- more efficient inserts for `chunks`
- more efficient inserts for `chunk_embeddings`
- fewer transactions per document where safe
- better concurrency configuration across extract and index queues

### D. Improve embedding efficiency

Indexing currently performs one embedding call per chunk.

The implementation should consider:

- batching embeddings where the provider and limits allow
- reducing repeated client setup cost
- lowering DB round-trips around usage accounting where safe
- preserving idempotency and failure recovery

### E. Strengthen ingestion reliability

Bulk ingestion needs stronger operational safety.

Important areas:

- idempotent reruns
- safe retry behavior
- better handling of partially completed work
- queue recovery after worker failure
- clearer status transitions
- better distinction between transient and permanent failures

### F. Make token-budget behavior predictable for ingestion

Embedding many documents can consume budget quickly.

The implementation must define:

- when ingestion should fail early
- whether work is reserved per document, per chunk group, or per batch
- how to avoid leaving stranded reservations
- how to surface budget-related failures clearly

### G. Prepare for multi-document retrieval

This task should not stop at ingestion-only thinking.

Even if multi-document query is implemented later, this task should preserve a clear path toward:

- querying a selected set of document IDs
- retrieving top chunks across those documents
- keeping citations tied to document and page metadata
- avoiding ingestion decisions that assume only one active document will ever be queried

## Recommended Phases

### Phase 1: Reliability and Pipeline Cleanup

Focus on making the existing ingestion flow safer before optimizing aggressively.

Targets:

- clarify document statuses and transitions
- strengthen retry and idempotency behavior
- improve error capture and logging
- ensure stale reservations are cleaned reliably
- make worker execution behavior clearer and more observable

### Phase 2: Throughput Improvements

Focus on reducing overhead and increasing throughput for `N` documents.

Targets:

- batch-oriented upload orchestration
- reduced DB round-trips
- more efficient chunk and embedding writes
- better worker concurrency and queue strategy
- improved rate-limit strategy for bulk ingestion paths

### Phase 3: Multi-Document Retrieval Readiness

Focus on preparing the backend for true cross-document querying.

Targets:

- query contract that can evolve from one `document_id` to many
- retrieval paths that can search across selected document sets
- metadata and observability that remain useful across multi-document contexts

## Acceptance Criteria

The task should be considered complete only when the implementation provides most or all of the following:

- The backend supports a clear high-volume ingestion strategy, not just repeated single-file flows
- Ingestion throughput is materially improved for many small PDFs
- Failures are visible per document and, if introduced, per ingestion run or batch
- Worker and queue behavior are more predictable under load
- Token-budget handling remains safe under concurrent ingestion
- The design does not block future multi-document retrieval
- Documentation is updated to explain the new ingestion model

## Suggested Validation

The implementation should be validated with backend-focused tests and measurement such as:

- ingesting dozens to hundreds of small PDFs
- mixed success and failure cases
- retry scenarios
- queue backlog scenarios
- low token-budget scenarios
- concurrent ingestion plus querying

Useful outputs:

- throughput before vs after
- average extraction time per document
- average indexing time per document
- queue wait time
- failure rate
- recovery behavior

## Risks to Watch

- making batching more complex than needed
- breaking idempotency
- overspending token budget during indexing
- adding bulk APIs without good observability
- optimizing for ingestion in a way that makes future multi-document retrieval harder

## Summary

This task is the foundation for turning the current asynchronous document pipeline into a backend that can ingest large numbers of small PDFs reliably and efficiently. The current code already has the right building blocks, but it still operates mostly as a single-document workflow repeated many times. The work here is to turn that into a deliberate high-volume ingestion architecture while keeping future multi-document retrieval in view.
