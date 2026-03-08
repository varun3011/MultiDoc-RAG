# Server

`server/` is the FastAPI backend for Enterprise RAG. It owns authentication, workspace resolution, document APIs, grounded query orchestration, token budget enforcement, and observability endpoints.

## Responsibilities

- validate Supabase JWTs
- resolve the caller's workspace
- manage document upload preparation and upload completion
- enqueue extraction and indexing jobs
- execute grounded RAG queries and SSE streaming queries
- track daily token usage with reserve/commit/release semantics
- expose query history, citations, chat sessions, and observability data

## Module Layout

```text
server/
├── app/
│   ├── main.py               # FastAPI app and router registration
│   ├── config.py             # environment settings and UTC helpers
│   ├── api/                  # route handlers
│   ├── core/                 # auth, retrieval, llm, embeddings, token budget
│   ├── db/                   # SQLAlchemy session, models, repositories
│   ├── schemas/              # request and response schemas
│   ├── storage/              # Supabase storage integration
│   └── utils/                # rate limiting and logging helpers
├── migrations/               # Alembic environment and revisions
├── tests/
├── requirements.txt
└── pyproject.toml
```

## API Surface

Routes registered in `app/main.py`:

- `GET /health`
- `GET /auth/me`
- `POST /workspaces`
- `GET /workspaces/me`
- `GET /documents`
- `GET /documents/{document_id}`
- `GET /documents/{document_id}/pages/{page_number}`
- `POST /documents/upload-prepare`
- `POST /documents/upload-complete`
- `POST /documents/{document_id}/retry`
- `POST /documents/{document_id}/reindex`
- `DELETE /documents/{document_id}`
- `POST /query`
- `POST /query/stream`
- `GET /citations/{chunk_id}`
- `GET /queries`
- `GET /queries/{query_id}`
- `POST /chats/sessions`
- `PATCH /chats/sessions/{session_id}`
- `GET /chats/sessions`
- `GET /chats/sessions/{session_id}`
- `GET /usage/today`
- `GET /usage/observability`

## Request Flow

### Auth and Workspace Resolution

1. Client sends `Authorization: Bearer <token>`.
2. `app/api/deps.py` validates bearer presence.
3. `app/core/auth.py` validates the JWT with Supabase.
4. `get_workspace_id()` resolves the current user's workspace.
5. Workspace-scoped routes operate only within that resolved `workspace_id`.

Primary files:
- `app/api/deps.py`
- `app/core/auth.py`
- `app/api/workspaces.py`

### Document Upload Lifecycle

```text
upload-prepare -> upload-complete -> enqueue extract -> extract pages
               -> enqueue index -> create chunks/embeddings -> ready
```

What the API does:
- validates file size and content type
- enforces document count limits
- creates placeholder document records
- issues signed upload URLs through Supabase Storage
- verifies uploaded object existence
- enqueues RQ jobs for extraction and indexing
- supports retry, reindex, and delete flows

Primary files:
- `app/api/documents.py`
- `app/core/storage.py`

### Query Lifecycle

```text
question -> embed question -> retrieve top-k chunks -> reserve tokens
         -> call LLM -> commit actual usage -> return answer + citations
```

What happens in code:
- question embedding comes from `app/core/embeddings.py`
- vector retrieval comes from `app/core/retrieval.py`
- grounded prompting and LLM calls come from `app/core/llm.py` and `app/core/prompts.py`
- token reservation and usage accounting come from `app/core/token_budget.py`
- query metadata is logged into `query_logs` when enabled

Primary files:
- `app/api/query.py`
- `app/api/query_stream.py`
- `app/core/retrieval.py`
- `app/core/llm.py`
- `app/core/token_budget.py`

## Core Subsystems

### Token Budget

The server enforces a daily workspace token limit using `workspace_daily_usage`.

Operations:
- `reserve_tokens()`
- `release_tokens()`
- `commit_usage()`
- `get_budget_status()`

Behavior:
- reservations are acquired before LLM work
- actual usage is committed after the model returns
- unused reserved tokens are released
- stale reservations are cleaned separately by the worker maintenance job

Primary file:
- `app/core/token_budget.py`

### Retrieval

The retrieval layer reads vectors from `chunk_embeddings` and chunk/page text from Postgres.

Current behavior:
- query embedding is turned into a `pgvector` literal
- cosine distance query returns top-k chunks for one document
- each result includes chunk text, page text, score, and token count

Primary file:
- `app/core/retrieval.py`

### Rate Limiting

Redis-backed per-workspace rate limiting is enforced in the API.

Current limits:
- queries: `100` requests / `60s`
- upload prepare: `10` requests / `60s`
- upload complete and retry/reindex mutations: `20` requests / `60s`

Primary files:
- `app/core/rate_limit.py`
- `app/utils/rate_limit.py`

## Data Model

The server depends on these core tables:

- `workspaces`
- `documents`
- `document_pages`
- `chunks`
- `chunk_embeddings`
- `workspace_daily_usage`
- `query_logs`
- `chat_sessions`

Current SQLAlchemy models are defined in `app/db/models.py`. Full schema scripts live under `../scripts/`.

## Environment

Minimal server environment:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=documents

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/enterprise_rag
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=sk-...

ENVIRONMENT=development
API_HOST=0.0.0.0
API_PORT=8000
DAILY_TOKEN_LIMIT=100000
RESERVATION_TTL_SECONDS=600
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
```

## Run

### Local

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker Compose

```bash
docker-compose up server
```

Health check:

```bash
curl http://localhost:8000/health
```

## Development Commands

```bash
cd server
pytest
ruff check .
black --check .
mypy app
```

## Notes for Contributors

- keep every data access path workspace-scoped
- do not bypass the token budget layer for query-time model usage
- document status transitions matter because workers and UI depend on them
- `app/core/chunking.py` is still a placeholder; worker-side chunking is currently the active path
- if you add endpoints, register them in `app/main.py` and add corresponding schemas
