# Project Context

## Purpose

This repository implements a workspace-scoped PDF RAG platform with three main parts:

- `client`: React + Vite frontend
- `server`: FastAPI API server
- `worker`: Redis/RQ background workers

The system lets a user sign in with Supabase, create a workspace, upload PDFs, process them asynchronously, and ask grounded questions against indexed content with citations.

## What Is Implemented

### 1. Authentication and Workspace Scoping

- The frontend uses Supabase Auth for login and signup.
- The API expects a bearer token and validates it against Supabase.
- Each authenticated user is resolved to a single workspace in v1-style flow.
- Workspace-scoped endpoints derive `workspace_id` on the server and use it to isolate data access.

Primary files:

- `client/src/context/AuthContext.tsx`
- `client/src/lib/supabase.ts`
- `server/app/api/deps.py`
- `server/app/core/auth.py`
- `server/app/api/workspaces.py`

### 2. Upload and Document Lifecycle

- The client requests an upload URL from the API.
- The API creates a placeholder document record and returns a signed Supabase Storage upload URL.
- The client uploads the PDF directly to storage instead of sending file bytes through the API.
- The client then calls `upload-complete`.
- The API verifies the uploaded object and enqueues extraction.
- Documents can later be deleted, retried after failure, or reindexed.

Current document statuses used by the code include:

- `pending_upload`
- `uploaded`
- `extracting`
- `indexing`
- `ready`
- `indexed`
- `failed`

Primary files:

- `client/src/lib/api.ts`
- `client/src/pages/UploadPage.tsx`
- `server/app/api/documents.py`
- `server/app/core/storage.py`

### 3. Background Ingestion

- Extraction and indexing are handled by Redis-backed RQ workers.
- `ingest_extract` downloads the PDF from Supabase Storage, extracts page text, and writes `document_pages`.
- `ingest_index` reads page text, chunks it per page, creates embeddings, stores chunks and vectors, and marks the document queryable.
- The worker process can listen to one or more named queues from environment configuration.

Primary files:

- `worker/worker.py`
- `worker/jobs/ingest_extract.py`
- `worker/jobs/ingest_index.py`

### 4. Retrieval and Querying

- The API supports both standard query and streaming query flows.
- A query is embedded with OpenAI embeddings.
- Retrieval uses pgvector similarity search over stored chunk embeddings.
- The answer prompt is strict-grounded: it is supposed to use only retrieved context and include citations.
- The API returns answer text, citations, and updated usage state.

Important current limitation:

- The implemented query flow is effectively single-document per request.
- The main query API accepts one `document_id`.
- Retrieval filters to one document at a time.
- The frontend chat flow also centers on one active document at a time.

This means the repository supports many uploaded documents, but not true cross-document querying yet.

Primary files:

- `server/app/api/query.py`
- `server/app/api/query_stream.py`
- `server/app/core/embeddings.py`
- `server/app/core/retrieval.py`
- `server/app/core/llm.py`
- `server/app/core/prompts.py`
- `client/src/pages/ChatPage.tsx`

### 5. Token Budget Control

- The server tracks daily token usage per workspace.
- Query requests estimate token usage before the LLM call.
- Tokens are reserved first, then committed after actual usage is known.
- Embedding work in indexing also reserves and commits token usage.
- The usage state includes `used`, `reserved`, `remaining`, and reset timing.

Primary files:

- `server/app/core/token_budget.py`
- `server/app/api/query.py`
- `worker/jobs/ingest_index.py`
- `server/app/api/usage.py`

### 6. Observability and History

- The system exposes usage and observability endpoints.
- Query logs are used to show totals, recent errors, latency stats, and top documents.
- The project also includes chat session APIs for saving and restoring conversations tied to a document/workspace context.

Primary files:

- `server/app/api/usage.py`
- `server/app/api/queries.py`
- `server/app/api/chats.py`
- `client/src/pages/ObservabilityPage.tsx`

## How It Works End to End

1. The user signs in through Supabase Auth.
2. The API resolves the user to a workspace.
3. The client calls `upload-prepare`.
4. The API creates a document placeholder and returns a signed upload URL.
5. The client uploads the PDF to Supabase Storage.
6. The client calls `upload-complete`.
7. The API enqueues extraction on Redis/RQ.
8. The extraction worker downloads the PDF, extracts text, and writes page rows.
9. The indexing worker chunks page text, generates embeddings, and stores vectors in Postgres/pgvector.
10. The document becomes queryable.
11. The user selects an indexed document and asks a question.
12. The API embeds the question, retrieves top chunks, reserves token budget, calls the LLM, and returns a grounded answer with citations.
13. Usage and query metadata are recorded for later inspection.

## Current Architecture Notes

- Storage is split across Supabase Storage for raw PDFs and Postgres for metadata, pages, chunks, embeddings, and usage data.
- Redis is used for background jobs and rate limiting behavior.
- OpenAI is used for embeddings and final answer generation.
- The codebase includes some schema-compatibility fallbacks, which suggests the schema and implementation have evolved over time.
- Some parts of the system are broader than the original architecture spec, including chat session APIs and observability views.

## Current Gaps Relative to a True Multi-Document RAG

These are the most important gaps to keep in mind for future tasks:

- Querying is still one-document-at-a-time.
- Retrieval does not yet search across a selected set of documents.
- The UI does not yet provide a multi-document query selection flow.
- Some status handling is adaptive because the code supports more than one schema/state shape.
- There are still TODOs and implementation rough edges, so this should be treated as a strong working baseline rather than a fully finished platform.

## Purpose of `docs/task`

The `docs/task` folder is intended to hold focused markdown files for future improvements. Each file should describe one concrete task, such as:

- multi-document retrieval
- multi-document query UI
- schema cleanup
- ingestion performance improvements
- better observability
- test coverage expansion
