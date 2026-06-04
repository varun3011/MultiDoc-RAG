# Task: Multi-Document Ingestion UX

## Status

Drafted from current codebase and aligned with the existing backend-focused ingestion tasks.

## Goal

Improve the user experience for ingesting multiple documents so users can upload, monitor, and recover large document sets with less manual effort and less confusion.

This task is focused on UX and frontend behavior, but it should assume the backend ingestion pipeline is also being improved through:

- `docs/task/ingestion-pipeline-scale-and-reliability.md`
- `docs/task/report-ingestion-workflow.md`

## Why This Task Exists

The current project supports document upload and asynchronous processing, but the user experience is still mostly shaped around one document at a time.

That is acceptable for light usage, but weak for real multi-document workflows where users want to:

- upload many PDFs together
- understand what is processing
- see what succeeded and what failed
- retry only failed items
- know when ingestion is complete
- avoid repeatedly checking each document manually

The backend can become more scalable, but users still need a workflow that makes large ingestion runs understandable and manageable.

## Current UX Baseline

Today the UX is centered on:

- uploading documents through the upload page
- viewing a document list
- auto-refreshing status while processing is active
- deleting a document
- selecting a ready document for chat

Primary files:

- `client/src/pages/UploadPage.tsx`
- `client/src/components/upload/UploadPanel`
- `client/src/components/layout/AppShell.tsx`
- `client/src/components/sidebar/DocumentSidebar`
- `client/src/lib/api.ts`

## What Is Missing Today

### 1. Multi-document ingestion does not feel like one workflow

Even if users upload multiple files, the experience still feels like repeated single-document actions instead of one coordinated ingestion session.

Missing pieces:

- no clear “upload run” mental model
- no grouped progress summary
- no batch success/failure view
- no simple completion state for a multi-file upload session

### 2. Weak progress visibility

The current UI shows document-level status, but the user still has to interpret a lot manually.

Missing pieces:

- progress summary for all files in the current upload session
- counts by status such as queued, extracting, indexing, ready, failed
- clear indication of remaining work
- clearer stage-based language

### 3. Failure handling is too manual

Users need a better path when some documents fail.

Missing pieces:

- clear failed-items section
- batch retry for failed documents
- better error surfacing
- easier distinction between temporary and permanent failures

### 4. No ingestion-oriented information architecture

The upload page is functional, but it does not yet behave like a control center for a larger ingestion operation.

Missing pieces:

- a top-level ingestion summary
- a strong distinction between “processing” and “ready”
- filters for large document sets
- more actionable states

### 5. Limited feedback during long-running ingestion

When many documents are processing, users need reassurance and clarity.

Missing pieces:

- meaningful progress indicators
- estimated state of completion
- better empty/loading/error states
- confirmation when the ingestion run is effectively done

## Desired End State

After this task is implemented, the multi-document ingestion UX should:

- feel like one coherent workflow
- make bulk upload easy to understand
- surface progress clearly across the whole set
- make failures easy to identify and recover from
- reduce the amount of manual checking users need to do
- scale to large document lists without becoming confusing

## Non-Goals

This task is not mainly about:

- backend queue or worker implementation
- low-level ingestion throughput improvements
- query UX for multi-document chat
- OCR support
- redesigning the whole application shell

## UX Requirements

The implementation should aim to provide most or all of the following.

### A. Multi-file upload flow

The upload surface should support a clear bulk-ingestion experience.

Possible requirements:

- select many PDFs in one action
- show the selected files before upload starts
- validate file limits before submission
- show which files are accepted vs rejected before processing begins

### B. Upload session summary

The page should communicate the current ingestion session as one thing.

Useful elements:

- total files selected
- total accepted
- total rejected
- total processing
- total completed
- total failed

### C. Better document-state presentation

Statuses should be understandable to non-technical users.

The UI should make it easy to distinguish:

- waiting to upload
- uploading
- queued
- extracting
- indexing
- ready
- failed

If backend statuses are more technical, the frontend should present cleaner labels and explanations.

### D. Better progress tracking

The UI should reduce guesswork.

Possible requirements:

- grouped counts by state
- progress banner or summary block
- live refresh behavior while ingestion is active
- clear indication when processing has fully completed

### E. Failure recovery UX

The user should be able to recover from failures without hunting through the interface.

Possible requirements:

- dedicated failed documents section or filter
- retry failed documents individually
- retry all failed documents
- visible error message previews

### F. Filtering and organization for larger sets

Once many documents exist, the list becomes harder to use.

Possible requirements:

- filter by status
- search by filename
- sort by newest, oldest, status, or recent activity
- separate “active ingestion” and “ready library” views

### G. Clear completion state

Users should know when they are done waiting.

Possible requirements:

- “all documents processed” signal
- summary of final outcomes
- next-step prompt such as “documents are ready for querying”

## Recommended UX Structure

The implementation can vary, but a strong version of this page likely has these layers:

1. Upload area
2. Current ingestion summary
3. Active processing section
4. Failed items section
5. Ready documents section
6. Controls for filtering and retrying

This keeps the page oriented around workflow instead of a flat undifferentiated table.

## Relationship With Backend Tasks

This task should be coordinated with backend ingestion work.

If the backend adds concepts like:

- batch upload endpoints
- ingestion runs
- grouped progress metadata
- richer status information

Then the frontend should use that directly instead of simulating everything client-side.

If the backend does not yet expose run-level concepts, the frontend may need an interim approach using document-level aggregation, but that should be treated as a temporary UX layer.

## Accessibility and Usability Expectations

The UX should also account for:

- clear visual hierarchy
- readable status labels
- keyboard-usable controls
- visible error messages
- understandable loading states
- responsive behavior on smaller screens

## Acceptance Criteria

This task should be considered complete only when the UI provides a clearly better workflow for multi-document ingestion, including most or all of the following:

- users can upload multiple documents in a way that feels like one workflow
- users can see overall ingestion progress without inspecting each document manually
- failed documents are easy to identify
- retry behavior is easy to discover and use
- ready documents are clearly separated from in-progress documents
- the experience remains understandable as document count grows

## Suggested Validation

The implementation should be validated with scenarios like:

- uploading 5 documents
- uploading 20 documents
- uploading 50 small documents
- mixed success and failure outcomes
- retrying only failed documents
- leaving and returning to the page during processing
- viewing the workflow on smaller screens

## Risks To Watch

- building a polished UI on top of weak backend status semantics
- showing misleading progress if true backend progress is not available
- overloading the page with too much status detail
- making bulk controls without clear safeguards
- treating large ingestion as just a bigger version of single-file upload

## Summary

This task upgrades the ingestion experience from a basic upload page into a workflow that supports real multi-document use. The goal is not only to let users send many files, but to help them understand what is happening, what failed, what finished, and what to do next.
