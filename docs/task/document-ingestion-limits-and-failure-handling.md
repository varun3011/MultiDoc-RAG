# Task: Document Ingestion Limits and Failure Handling

## Status

Drafted from current codebase and aligned with the ingestion improvement work.

## Goal

Define and implement clear ingestion requirements for documents, and make ingestion failure handling predictable, visible, and recoverable.

This task is about two things:

1. enforcing document limits clearly
2. handling ingestion failures properly from validation through retry

## Why This Task Exists

The project already has some limits and failure paths, but they are not yet treated as a fully defined product and engineering workflow.

Right now the system has partial support for:

- file size checks
- content type checks
- document status updates
- retry and reindex actions
- worker-side failure marking

But the limits and failure rules still need to be made more explicit, more consistent, and easier to operate.

## Scope

This task should cover:

- document upload requirements
- validation rules
- user-safe rejection behavior
- ingestion-time failure behavior
- status transitions for failures
- error classification
- retry behavior
- observability of failures

This task is closely related to:

- `docs/task/ingestion-pipeline-scale-and-reliability.md`
- `docs/task/report-ingestion-workflow.md`
- `docs/task/multi-document-ingestion-ux.md`

## Current Baseline

Based on the current code and project context, the intended constraints are small PDFs only.

The main working assumptions are:

- PDF only
- text-based PDFs only
- small files
- limited page count

The codebase already contains some existing limits, including:

- max file size validation
- allowed content type validation
- workspace document count limit
- query length limit

Primary files:

- `server/app/config.py`
- `server/app/api/documents.py`
- `server/app/schemas/documents.py`
- `worker/jobs/ingest_extract.py`
- `worker/jobs/ingest_index.py`

## Requirements To Make Explicit

The implementation should define and enforce clear ingestion requirements.

### File Requirements

At minimum, the system should have explicit rules for:

- allowed file type: PDF only
- maximum file size
- maximum page count
- supported PDF type: text-based only
- invalid filename handling

Recommended baseline for this project:

- maximum file size: `10 MB`
- maximum page count: `10 pages`

If a different value is chosen, it should still be documented clearly and enforced consistently.

### Validation Timing

The system should define where each rule is enforced.

Examples:

- before upload begins
- at upload-prepare
- at upload-complete
- during extraction
- after extraction if document shape is invalid

The goal is to reject early where possible, but still re-check critical constraints after upload so the system is safe against mismatches and bad clients.

## What Is Missing Today

### 1. Limits are not fully centralized as product rules

Some constraints exist, but they are not yet fully presented as a clean ingestion policy.

Missing areas:

- one clear source of truth for file size and page count
- consistent enforcement across API and workers
- consistent error messages
- clearer documentation for agents and developers

### 2. Page-count handling needs to be explicit

If the product rule is “small PDFs only,” then page-count enforcement should be unambiguous.

Missing areas:

- guaranteed page-count validation path
- clear behavior when page count exceeds limit
- clear status and error message when rejection happens after extraction starts

### 3. Failure states need stronger definition

The system marks documents as failed, but failure behavior still needs to be defined more rigorously.

Missing areas:

- consistent failure categories
- clearer distinction between validation failure and processing failure
- clearer distinction between retryable and non-retryable failures
- stronger rules for partial progress cleanup

### 4. Error messages need to be more actionable

Users and operators need clearer reasons for failure.

Missing areas:

- “file too large”
- “page count exceeded”
- “unsupported PDF type”
- “text extraction failed”
- “embedding budget exceeded”
- “storage object missing”

These should be structured and consistent, not just raw exception text where avoidable.

### 5. Retry behavior needs stronger rules

Retries should be allowed only when safe and meaningful.

Missing areas:

- which failures can be retried directly
- which failures require reupload
- which failures should stay terminal
- how partial extracted/chunked data is cleaned up before retry

## Desired End State

After this task is implemented, the ingestion system should behave like this:

- document requirements are clearly defined
- invalid files are rejected as early as possible
- critical limits are revalidated during ingestion where necessary
- failures are categorized clearly
- document statuses reflect the true stage and outcome
- retries are safe and predictable
- operators and future agents can understand why a document failed

## Failure Categories To Introduce

The implementation should define failure categories or equivalent structured error types.

Suggested categories:

- validation failure
- upload/storage failure
- extraction failure
- page-limit failure
- unsupported-content failure
- indexing failure
- budget failure
- transient infrastructure failure

These do not have to be stored exactly with these names, but the system should have a structured way to reason about them.

## Recommended Handling Rules

### A. Validation Failures

Examples:

- wrong content type
- file too large
- invalid filename
- too many documents in workspace

Expected behavior:

- reject before ingestion starts
- do not enqueue workers
- return clear API errors

### B. Upload Verification Failures

Examples:

- object missing from storage
- bucket/path mismatch

Expected behavior:

- do not start extraction
- leave a clear document state or reject completion safely
- avoid creating stuck documents

### C. Page Limit Failures

Examples:

- uploaded PDF exceeds max page count

Expected behavior:

- detect during extraction or immediately after page inspection
- mark document failed with a clear message
- avoid indexing
- make retry behavior clear

### D. Unsupported Content Failures

Examples:

- scanned PDF with no extractable text
- effectively empty extracted text

Expected behavior:

- mark as failed with a clear reason
- do not continue indexing if the content is unusable
- tell the user the document type is not supported

### E. Indexing Failures

Examples:

- chunk write failure
- embedding API failure
- vector insert failure

Expected behavior:

- mark document failed
- release reserved budget correctly
- preserve enough information to retry safely

### F. Budget Failures

Examples:

- insufficient token budget for embeddings

Expected behavior:

- fail safely
- leave no stranded reservations
- return or store a clear failure reason
- support retry later if budget resets

## Retry Policy Requirements

The task should define a retry policy.

Suggested expectations:

- retry allowed for transient infrastructure failures
- retry allowed for indexing failures when cleanup is safe
- retry not useful for file-size or page-limit failures without changing the document
- retry not useful for unsupported scanned PDFs unless product support changes

The UI can later reflect these distinctions, but the backend behavior should be defined first.

## Data and Status Considerations

The implementation should review whether the current document status model is sufficient.

Important questions:

- is one generic `failed` status enough
- should failure type be stored separately
- should retryability be derivable from failure type
- should error messages be structured in addition to human-readable text

This task does not require a specific schema design, but it should leave the system easier to reason about.

## Acceptance Criteria

This task should be considered complete only when most or all of the following are true:

- file-size rules are explicit and enforced consistently
- page-count rules are explicit and enforced consistently
- unsupported document types are rejected clearly
- ingestion failures produce meaningful failure states
- retry behavior is safe and defined
- budget-related failures are handled cleanly
- failure reasons are useful for both users and operators
- documentation reflects the final rules

## Suggested Validation

The implementation should be tested with cases like:

- valid PDF within size and page limits
- PDF larger than allowed size
- PDF with more than allowed pages
- scanned or non-extractable PDF
- missing uploaded object
- embedding failure
- low token-budget ingestion
- retry of retryable failure
- attempted retry of non-retryable failure

## Risks To Watch

- validating too late and wasting worker capacity
- weak error messages that hide the real cause
- allowing unsafe retries that create inconsistent state
- failing to release reserved tokens on error paths
- mixing user-facing errors and raw internal exceptions

## Summary

This task makes document ingestion requirements explicit and failure handling dependable. The goal is to make the system strict about what it accepts, clear about why a document failed, and safe when retries or recovery actions are needed.
