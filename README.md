# Enterprise RAG

Enterprise RAG is a workspace-scoped document intelligence platform for uploading PDFs, extracting text, indexing chunks with embeddings, and answering questions with grounded citations.

The repository is organized as a monorepo with three application surfaces:
- `server`: FastAPI API and core RAG orchestration
- `worker`: Redis/RQ background jobs for extraction, indexing, and maintenance
- `client`: React + Vite frontend with Supabase authentication

This README is written for the current codebase. It explains what the system does today, how the services interact, and how the architecture is intended to scale like an enterprise application.

## Table of Contents

- [Why This Exists](#why-this-exists)
- [What The System Does](#what-the-system-does)
- [System Architecture](#system-architecture)
- [How It Works End to End](#how-it-works-end-to-end)
- [Repository Layout](#repository-layout)
- [API Surface](#api-surface)
- [Data Model](#data-model)
- [Limits and Controls](#limits-and-controls)
- [Local Development](#local-development)
- [Environment Variables](#environment-variables)
- [Operational Notes](#operational-notes)
- [Current Status](#current-status)
- [Roadmap](#roadmap)

## Why This Exists

Typical RAG demos stop at a single script that embeds documents and sends a prompt to an LLM. That is not enough for a production system.

This project is built around the concerns that matter in enterprise environments:
- strict workspace isolation
- authenticated access with bearer tokens
- asynchronous ingestion so uploads do not block API requests
- token budget enforcement with reservation and commit semantics
- query logging and observability
- repeatable local development with Docker Compose
- clean separation between API, workers, storage, and UI

## What The System Does

At a high level, the platform supports this flow:
1. A user signs in with Supabase Auth.
2. The user creates a workspace.
3. The client requests a signed upload URL from the API.
4. A PDF is uploaded to Supabase Storage.
5. The API confirms the upload and enqueues background jobs.
6. Workers extract page text, split it into chunks, and generate embeddings.
7. The document becomes queryable.
8. The user asks a question against a document.
9. The API embeds the question, retrieves the most relevant chunks, calls the LLM with grounded context, and returns an answer with citations.
10. Usage, latency, and errors are recorded for observability.

## System Architecture

### Logical View

```mermaid
flowchart LR
    User[User] --> Client[React Client]
    Client --> Auth[Supabase Auth]
    Client -->|JWT| API[FastAPI API]

    API --> DB[(PostgreSQL + pgvector)]
    API --> Redis[(Redis)]
    API --> Storage[Supabase Storage]
    API --> OpenAI[OpenAI API]

    API -->|enqueue| ExtractQ[RQ ingest_extract]
    API -->|enqueue| IndexQ[RQ ingest_index]

    ExtractQ --> ExtractWorker[Extraction Worker]
    IndexQ --> IndexWorker[Indexing Worker]

    ExtractWorker --> Storage
    ExtractWorker --> DB
    ExtractWorker --> Redis

    IndexWorker --> DB
    IndexWorker --> OpenAI

    API --> Client
```

### Runtime Topology

```text
+--------------------+        +---------------------+
| React Client       |        | Supabase            |
| Vite app           |        | Auth + Storage      |
+---------+----------+        +----------+----------+
          |                              ^
          | JWT / signed upload flow     |
          v                              |
+---------+-------------------------------------------+
| FastAPI Server                                        |
| - auth validation                                     |
| - workspace-scoped APIs                               |
| - query orchestration                                 |
| - token budget checks                                 |
+---------+----------------------+----------------------+
          |                      |
          | SQL                  | enqueue jobs
          v                      v
+---------+----------+   +-------+----------------------+
| PostgreSQL         |   | Redis / RQ                  |
| pgvector           |   | rate limiting + job queues  |
+---------+----------+   +-------+----------------------+
          ^                      |
          |                      v
          |              +-------+----------------------+
          |              | Worker Processes            |
          |              | - extract PDF text          |
          |              | - chunk pages               |
          |              | - create embeddings         |
          |              | - cleanup reservations      |
          |              +------------------------------+
          |
          +------------ OpenAI embeddings / chat model
```

### Enterprise Design Characteristics

- API and worker responsibilities are separated.
- Document ingestion is asynchronous and queue-backed.
- Every major operation is scoped by `workspace_id`.
- Token usage is tracked centrally by day.
- Redis is used both for rate limiting and job execution.
- Vector retrieval stays in Postgres with `pgvector` instead of introducing another data store.
- The frontend is a separate deployable artifact.

## How It Works End to End

### 1. Authentication and Workspace Resolution

The client authenticates with Supabase and sends the bearer token to the API. The server validates the token and derives the current user. Workspace-scoped endpoints then resolve the user's workspace before accessing documents or usage records.

Primary files:
- `client/src/lib/supabase.ts`
- `server/app/api/deps.py`
- `server/app/core/auth.py`
- `server/app/api/workspaces.py`

### 2. Upload and Ingestion Pipeline

The upload pipeline is designed so the API never has to receive the PDF bytes directly.

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant S as Supabase Storage
    participant R as Redis/RQ
    participant W1 as Extract Worker
    participant W2 as Index Worker
    participant D as PostgreSQL

    C->>A: POST /documents/upload-prepare
    A->>D: create placeholder document row
    A-->>C: signed upload URL + storage path
    C->>S: upload PDF directly
    C->>A: POST /documents/upload-complete
    A->>R: enqueue extract job
    R->>W1: ingest_extract
    W1->>S: download PDF
    W1->>D: write document_pages
    W1->>R: enqueue ingest_index
    R->>W2: ingest_index
    W2->>D: write chunks
    W2->>D: write chunk_embeddings
    W2->>D: mark document ready/indexed
```

What happens in practice:
- `upload-prepare` validates file size, content type, workspace limits, and idempotency.
- `upload-prepare-batch` can prepare many small PDFs as one ingestion run and returns per-file results.
- The API stores a placeholder document record and returns a signed storage URL.
- `upload-complete` and `upload-complete-batch` confirm uploaded objects and enqueue extraction jobs.
- `ingest_extract` downloads the PDF and writes extracted page text into `document_pages`.
- `ingest_index` chunks page text, generates embeddings in batches, stores vectors, and marks the document ready.

Primary files:
- `server/app/api/documents.py`
- `server/app/core/storage.py`
- `worker/jobs/ingest_extract.py`
- `worker/jobs/ingest_index.py`

### 3. Query Pipeline

The query flow is grounded retrieval, not free-form generation.

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant D as PostgreSQL/pgvector
    participant O as OpenAI

    C->>A: POST /query or POST /query/stream
    A->>A: validate workspace, document, limits
    A->>O: embed question
    A->>D: retrieve top-k chunks by vector similarity
    A->>A: reserve token budget
    A->>O: generate grounded answer
    A->>A: commit actual usage and release remainder
    A->>D: write query log
    A-->>C: answer + citations + usage
```

What the server does:
- embeds the question with `text-embedding-3-small`
- retrieves top chunks from `chunk_embeddings` and `chunks`
- builds a grounded prompt using retrieved content
- reserves the estimated token budget before the LLM call
- commits actual usage after the response returns
- logs citations, latency, and token usage

Primary files:
- `server/app/api/query.py`
- `server/app/api/query_stream.py`
- `server/app/core/retrieval.py`
- `server/app/core/embeddings.py`
- `server/app/core/llm.py`
- `server/app/core/token_budget.py`

### 4. Usage and Observability

The system exposes both real-time usage and an observability summary.

Current observability coverage includes:
- daily token usage and remaining budget
- total query count
- 24-hour query volume and error rate
- latency statistics
- document status summary
- top queried documents
- recent query failures

Primary files:
- `server/app/api/usage.py`
- `server/app/db/models.py`
- `worker/jobs/maintenance.py`

## Repository Layout

```text
enterprise-rag/
├── client/                  # React + Vite frontend
├── server/                  # FastAPI API, core logic, DB layer
├── worker/                  # Redis/RQ workers and maintenance jobs
├── scripts/                 # DB bootstrap and utility scripts
├── infrastructure/          # Infrastructure placeholders
├── docker-compose.yml       # Full local stack
├── docker-compose.prod.yml  # Production-style compose file
├── AGENTS.md                # Architecture contract and implementation notes
└── README.md
```

### Important Directories

- `server/app/api`: REST and streaming endpoints
- `server/app/core`: auth, retrieval, embeddings, prompts, token budget
- `server/app/db`: SQLAlchemy models and DB session setup
- `server/app/schemas`: request and response models
- `worker/jobs`: extraction, indexing, maintenance jobs
- `client/src/pages`: authenticated and public application pages
- `client/src/components`: UI modules for upload, chat, usage, and layout

## API Surface

### Health and Auth

- `GET /health`
- `GET /auth/me`

### Workspace

- `POST /workspaces`
- `GET /workspaces/me`

### Documents

- `GET /documents`
- `GET /documents/{document_id}`
- `GET /documents/{document_id}/pages/{page_number}`
- `POST /documents/upload-prepare`
- `POST /documents/upload-prepare-batch`
- `POST /documents/upload-complete`
- `POST /documents/upload-complete-batch`
- `GET /documents/ingestion-runs/{run_id}`
- `GET /documents/ingestion-queues`
- `POST /documents/{document_id}/retry`
- `POST /documents/{document_id}/reindex`
- `DELETE /documents/{document_id}`

### Query and Retrieval

- `POST /query`
- `POST /query/stream`
- `GET /citations/{chunk_id}`
- `GET /queries`
- `GET /queries/{query_id}`

### Chat Sessions

- `POST /chats/sessions`
- `PATCH /chats/sessions/{session_id}`
- `GET /chats/sessions`
- `GET /chats/sessions/{session_id}`

### Usage and Observability

- `GET /usage/today`
- `GET /usage/observability`

## Data Model

Core tables in the current implementation:

- `workspaces`: tenant root for all user content
- `ingestion_runs`: batch upload and ingestion progress grouping
- `documents`: uploaded PDF metadata and pipeline status
- `document_pages`: extracted page text
- `chunks`: page-bounded text chunks used for retrieval
- `chunk_embeddings`: vector embeddings stored in `pgvector`
- `workspace_daily_usage`: daily token accounting with reserved and used buckets
- `query_logs`: query history, citations, latency, and token metrics
- `chat_sessions`: persisted chat metadata and messages

### Status Lifecycle

Document lifecycle in the current codebase:

```text
pending_upload/uploading -> uploaded -> extracting -> indexing -> ready/indexed
                                                  \-> failed
```

## Limits and Controls

Current enforced limits from the application config and rate limiter:

- `1` workspace per user
- up to `100` documents per workspace
- maximum file size: `10 MB`
- maximum PDF page count: `10`
- maximum files per backend batch upload: `50`
- supported upload type: `application/pdf`
- maximum query length: `500` characters
- retrieval depth: `top_k = 5`
- LLM max output tokens: `2000`
- daily token limit: `100000` tokens per workspace
- upload prepare rate limit: `10` requests per minute per workspace
- upload complete rate limit: `20` requests per minute per workspace
- query rate limit: `100` requests per minute per workspace

Bulk ingestion endpoints have separate rate limits from single-file endpoints:
- batch upload prepare: `5` requests per minute per workspace
- batch upload complete: `10` requests per minute per workspace

### Token Budget Model

The token budget is managed with reservation semantics so concurrent requests do not overspend the daily allowance.

Flow:
1. Estimate query embedding + prompt + max output cost.
2. Reserve the estimated tokens.
3. Execute the LLM call.
4. Commit actual tokens used.
5. Release any unused reservation.
6. Periodically clean stale reservations.

This logic is implemented in `server/app/core/token_budget.py` and `worker/jobs/maintenance.py`.

## Local Development

### Prerequisites

- Docker and Docker Compose
- Node.js 20+ if running the client outside Docker
- Python 3.11 if running the API or worker outside Docker
- A Supabase project
- An OpenAI API key for embeddings and answer generation

### Quick Start With Docker Compose

1. Create your environment file.

```bash
cp .env.example .env
```

2. Fill in at least these values:

```bash
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_ANON_KEY=
SUPABASE_JWT_SECRET=
OPENAI_API_KEY=
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/enterprise_rag
REDIS_URL=redis://localhost:6379/0
```

3. Start the stack.

```bash
docker-compose up --build
```

4. Open the services:
- client: `http://localhost:5173`
- api: `http://localhost:8000`
- rq dashboard: `http://localhost:9181`

### Useful Commands

```bash
# start everything
docker-compose up

# rebuild and start
docker-compose up --build

# stop services
docker-compose down

# stop and remove volumes
docker-compose down -v

# run DB migrations from the server container
docker-compose exec server alembic upgrade head

# view server logs
docker-compose logs -f server

# view worker logs
docker-compose logs -f worker-extract
docker-compose logs -f worker-index
```

### Run Services Individually

Server:

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Client:

```bash
cd client
npm install
npm run dev -- --host 0.0.0.0
```

Worker:

```bash
cd worker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
QUEUE_NAME=ingest_extract python worker.py
```

## Environment Variables

Root `.env.example` is the primary template for local development.

### Required Core Variables

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_JWT_SECRET=your-jwt-secret
SUPABASE_STORAGE_BUCKET=documents

OPENAI_API_KEY=sk-...

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/enterprise_rag
REDIS_URL=redis://localhost:6379/0
```

### Useful Application Variables

```bash
ENVIRONMENT=development
API_HOST=0.0.0.0
API_PORT=8000
DAILY_TOKEN_LIMIT=100000
RESERVATION_TTL_SECONDS=600
LOG_EACH_QUERY=false
EMBEDDING_MODEL=text-embedding-3-small
MAX_FILE_SIZE_BYTES=10485760
MAX_PDF_PAGE_COUNT=10
MIN_EXTRACTED_TEXT_CHARS=1
MAX_BULK_UPLOAD_FILES=50
EMBEDDING_BATCH_SIZE=32
OPENAI_EMBEDDING_TIMEOUT_SECONDS=300
INGEST_EXTRACT_JOB_TIMEOUT_SECONDS=900
INGEST_INDEX_JOB_TIMEOUT_SECONDS=1800
VITE_API_URL=http://localhost:8000
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
```

## Operational Notes

### Workspace Isolation

The system is designed around `workspace_id` as the isolation boundary. Document access, usage tracking, retrieval, and query logs are all scoped to a workspace.

### Storage Strategy

PDF binaries live in Supabase Storage. Extracted text, chunks, metadata, and embeddings live in Postgres.

### Vector Search Strategy

Embeddings are stored in `chunk_embeddings.embedding` using `pgvector`. Retrieval uses cosine distance and returns the most relevant chunk candidates for a single document.

### Failure Recovery

- failed documents can be retried with `POST /documents/{document_id}/retry`
- already processed documents can be reindexed with `POST /documents/{document_id}/reindex`
- stale token reservations can be cleared by the maintenance job
- RQ extraction/indexing jobs use explicit timeouts and a failure callback so timed-out jobs mark documents `failed` instead of leaving them stuck in processing
- document deletion removes metadata first, then attempts storage cleanup

### Frontend Application Areas

Current routed pages in the client:
- `/login`
- `/signup`
- `/workspace`
- `/app/upload`
- `/app/chat`
- `/app/observability`
- `/app/workspace`

## Current Status

The repository is beyond a scaffold. These capabilities are already present in code:

- JWT-backed auth integration with Supabase
- one-workspace-per-user model
- signed upload preparation and upload completion
- background extraction and indexing jobs
- page text persistence
- chunk persistence and vector embedding storage
- grounded query endpoint
- streaming query endpoint using SSE
- citation source retrieval
- query history APIs
- chat session APIs
- usage and observability endpoints
- Docker-based local runtime

Known gaps or areas still being hardened:
- not every table in the architecture contract is represented yet in SQLAlchemy models
- `server/app/core/chunking.py` remains a placeholder while worker-side chunking is active
- production deployment still needs full operational hardening, secrets handling, and CI maturity
- test coverage is still light for end-to-end ingestion and retrieval

## Roadmap

Near-term improvements that fit the current architecture:

1. Move chunking into a shared core module so API and workers use one implementation.
2. Expand integration tests around upload, extraction, indexing, and query behavior.
3. Add stronger metrics, worker lifecycle hooks, and scheduled maintenance execution.
4. Harden migration coverage for all current tables and status transitions.
5. Expand query scope from single-document search to selected multi-document search where needed.
6. Improve production deployment docs and CI/CD validation.

## Related Docs

- `AGENTS.md`: architecture contract and implementation guidance
- `server/README.md`: server-specific notes
- `worker/README.md`: worker-specific notes
- `client/README.md`: client-specific notes
