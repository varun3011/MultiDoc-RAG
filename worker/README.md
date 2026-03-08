# Worker

`worker/` runs asynchronous background jobs for Enterprise RAG. It is responsible for the long-running work that should not happen in the request path: PDF extraction, chunk indexing, embedding generation, and maintenance cleanup.

## Responsibilities

- consume RQ jobs from Redis
- extract page text from uploaded PDFs
- persist `document_pages`
- split page text into chunks
- generate embeddings for chunks
- mark documents as `ready` or `failed`
- clean stale token reservations

## Module Layout

```text
worker/
├── jobs/
│   ├── ingest_extract.py     # PDF download and page extraction
│   ├── ingest_index.py       # chunking and embedding generation
│   └── maintenance.py        # stale reservation cleanup
├── shared/                   # shared package placeholder
├── worker.py                 # RQ worker entrypoint
├── requirements.txt
└── tests/
```

## Queue Model

The worker process reads one or more queue names from `QUEUE_NAME` or the first CLI argument.

Current queue names:
- `ingest_extract`
- `ingest_index`

Entrypoint behavior in `worker.py`:
- connect to Redis
- create `Queue` objects for the configured names
- start an RQ `Worker`

Example:

```bash
cd worker
QUEUE_NAME=ingest_extract REDIS_URL=redis://localhost:6379/0 python worker.py
```

## Job Flows

### `jobs/ingest_extract.py`

Purpose:
- download the uploaded PDF from Supabase Storage
- read it with `pypdf`
- extract per-page text
- replace any previous `document_pages` rows for idempotency
- update the document status to `indexing`
- enqueue `ingest_index`

Current behavior:
- extraction is page-based
- temporary PDF file is written under `/tmp`
- failures mark the document as `failed`
- if the schema does not include `extracting`, the code falls back to `indexing`

### `jobs/ingest_index.py`

Purpose:
- load extracted page text
- split each page into chunks
- insert chunk rows
- reserve token budget per chunk embedding
- call OpenAI embeddings
- insert vectors into `chunk_embeddings`
- commit token usage
- mark the document as `ready` or `indexed`

Current chunking behavior:
- page-bounded chunks
- target chunk size: `500` tokens
- overlap: `100` tokens
- `tiktoken` when available, character approximation fallback otherwise

Failure behavior:
- budget exhaustion marks the document `failed`
- any outstanding reservations are released on failure
- chunk rows are rebuilt idempotently for the document during reindex

### `jobs/maintenance.py`

Purpose:
- clear stale rows in `workspace_daily_usage` where tokens remain reserved beyond TTL

Current behavior:
- supports PostgreSQL and SQLite variants
- reads `DATABASE_URL` and `RESERVATION_TTL_SECONDS`
- returns affected row count

Example:

```bash
cd worker
python -c "from jobs.maintenance import cleanup_stale_reservations; print(cleanup_stale_reservations())"
```

## Runtime Flow

```text
API upload-complete
  -> enqueue ingest_extract
  -> extract pages from PDF
  -> enqueue ingest_index
  -> chunk pages
  -> embed chunks
  -> mark document ready
```

## Environment

Minimal worker environment:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/enterprise_rag
REDIS_URL=redis://localhost:6379/0
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_KEY=your-service-role-key
OPENAI_API_KEY=sk-...
QUEUE_NAME=ingest_extract
RESERVATION_TTL_SECONDS=600
EMBEDDING_MODEL=text-embedding-3-small
```

## Run

### Local

```bash
cd worker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
QUEUE_NAME=ingest_extract python worker.py
```

Run the index queue:

```bash
QUEUE_NAME=ingest_index python worker.py
```

Run both queues in one process:

```bash
QUEUE_NAME=ingest_extract,ingest_index python worker.py
```

### Docker Compose

```bash
docker-compose up worker-extract worker-index
```

## Development Notes

- worker jobs are stateful with respect to document status transitions; keep them explicit
- release reserved tokens on every failure path
- chunking currently lives in `jobs/ingest_index.py`, not in shared core yet
- `worker/shared/` exists for future code consolidation, but the active shared path in local compose is the mounted server app code
- if you add new jobs, ensure the API enqueues them by import path and that the queue name is wired in Compose
