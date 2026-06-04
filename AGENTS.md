# AGENTS.md
# Enterprise RAG System (v1) — Production-Ready Architecture

## Context First

Before planning changes or implementing tasks, read `docs/project-context.md` to understand the current codebase, what is already implemented, and the known gaps between the architecture goal and the as-built system.

## Task Routing And Order

When a user asks what should be implemented next, what a task means, or asks to work on a specific improvement area, use the task docs in `docs/task/` as the source of truth for scope, expected behavior, and implementation direction.

Map requests to task files like this:

- Backend ingestion scalability, throughput, worker behavior, queue behavior, orchestration, or ingestion architecture:
  - `docs/task/ingestion-pipeline-scale-and-reliability.md`
- Reviewing or testing the ingestion workflow after implementation, finding gaps, bottlenecks, or follow-up improvements:
  - `docs/task/report-ingestion-workflow.md`
- Frontend or workflow improvements for uploading and managing many documents:
  - `docs/task/multi-document-ingestion-ux.md`
- File-size limits, page-count limits, validation rules, ingestion rejection behavior, retry rules, and failure handling:
  - `docs/task/document-ingestion-limits-and-failure-handling.md`

Recommended order of execution:

1. Read `docs/project-context.md`
2. Implement `docs/task/document-ingestion-limits-and-failure-handling.md`
3. Implement `docs/task/ingestion-pipeline-scale-and-reliability.md`
4. Run the evaluation/report work from `docs/task/report-ingestion-workflow.md`
5. Implement `docs/task/multi-document-ingestion-ux.md`

Ordering rules:

- Do not start UX-first if backend ingestion behavior, limits, and failure semantics are still changing.
- Treat document limits and failure handling as foundational because they affect API contracts, worker behavior, retry rules, and user messaging.
- Treat the ingestion scale/reliability task as the main backend improvement track.
- Use the report task after backend ingestion work to measure what is still weak before starting major UX refinement.
- UX work should reflect the actual backend model rather than inventing a frontend-only approximation unless clearly marked as temporary.

If the user asks to implement one task directly, still read the dependent earlier task docs when needed so the work stays aligned with the intended sequence.

**Version**: 1.0 Final  
**Last Updated**: 2024-02-13  
**Status**: Locked for Implementation

---

## Table of Contents
1. [Project Structure](#1-project-structure)
2. [Docker Setup](#2-docker-setup)
3. [Core Principles](#3-core-principles)
4. [Platform Stack](#4-platform-stack)
5. [Workspace Model](#5-workspace-model)
6. [Hard Limits & Constraints](#6-hard-limits--constraints)
7. [Token Budget System](#7-token-budget-system)
8. [Chunking & Retrieval Strategy](#8-chunking--retrieval-strategy)
9. [Database Schema](#9-database-schema)
10. [Upload Architecture](#10-upload-architecture)
11. [Ingestion Pipeline](#11-ingestion-pipeline)
12. [Query System (RAG)](#12-query-system-rag)
13. [API Endpoints](#13-api-endpoints)
14. [Worker Configuration](#14-worker-configuration)
15. [Error Handling & Retries](#15-error-handling--retries)
16. [Monitoring & Health Checks](#16-monitoring--health-checks)
17. [Security & Rate Limiting](#17-security--rate-limiting)
18. [Maintenance & Operations](#18-maintenance--operations)

---

## 1) Project Structure

### Repository Layout
```
enterprise-rag/
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
├── .gitignore
├── README.md
├── AGENTS.md
│
├── server/                          # FastAPI Backend
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .dockerignore
│   ├── pyproject.toml
│   ├── pytest.ini
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app entry point
│   │   ├── config.py               # Settings & environment
│   │   │
│   │   ├── api/                    # API routes
│   │   │   ├── __init__.py
│   │   │   ├── deps.py             # Dependencies (auth, db)
│   │   │   ├── workspaces.py
│   │   │   ├── documents.py
│   │   │   ├── query.py
│   │   │   └── usage.py
│   │   │
│   │   ├── core/                   # Core business logic
│   │   │   ├── __init__.py
│   │   │   ├── auth.py             # JWT validation
│   │   │   ├── token_budget.py     # Token reservation logic
│   │   │   ├── chunking.py         # Text chunking
│   │   │   ├── embeddings.py       # OpenAI embeddings
│   │   │   └── retrieval.py        # Vector search
│   │   │
│   │   ├── db/                     # Database layer
│   │   │   ├── __init__.py
│   │   │   ├── session.py          # DB connection
│   │   │   ├── models.py           # SQLAlchemy models
│   │   │   └── repositories/
│   │   │       ├── workspace.py
│   │   │       ├── document.py
│   │   │       ├── chunk.py
│   │   │       └── usage.py
│   │   │
│   │   ├── schemas/                # Pydantic schemas
│   │   │   ├── __init__.py
│   │   │   ├── workspace.py
│   │   │   ├── document.py
│   │   │   ├── query.py
│   │   │   └── usage.py
│   │   │
│   │   ├── storage/                # Supabase Storage
│   │   │   ├── __init__.py
│   │   │   └── client.py
│   │   │
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── rate_limit.py
│   │       └── logging.py
│   │
│   ├── migrations/                  # Alembic migrations
│   │   ├── env.py
│   │   └── versions/
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_api/
│       ├── test_core/
│       └── test_db/
│
├── worker/                          # RQ Workers
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .dockerignore
│   │
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── ingest_extract.py       # PDF extraction job
│   │   ├── ingest_index.py         # Embedding generation job
│   │   └── maintenance.py          # Cleanup jobs
│   │
│   ├── shared/                      # Shared with server
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── db/                     # Same models as server
│   │   └── core/                   # Same logic as server
│   │
│   ├── worker.py                    # Worker entry point
│   └── tests/
│
├── client/                          # React Frontend
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   │
│   ├── public/
│   │   └── assets/
│   │
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── vite-env.d.ts
│   │   │
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── Header.tsx
│   │   │   │   ├── Sidebar.tsx
│   │   │   │   └── Layout.tsx
│   │   │   │
│   │   │   ├── documents/
│   │   │   │   ├── DocumentList.tsx
│   │   │   │   ├── DocumentUpload.tsx
│   │   │   │   ├── DocumentStatus.tsx
│   │   │   │   └── DocumentCard.tsx
│   │   │   │
│   │   │   ├── query/
│   │   │   │   ├── QueryInput.tsx
│   │   │   │   ├── QueryResults.tsx
│   │   │   │   ├── Citation.tsx
│   │   │   │   └── DocumentSelector.tsx
│   │   │   │
│   │   │   └── usage/
│   │   │       ├── TokenMeter.tsx
│   │   │       ├── UsageChart.tsx
│   │   │       └── UsageBreakdown.tsx
│   │   │
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Documents.tsx
│   │   │   ├── Query.tsx
│   │   │   ├── Usage.tsx
│   │   │   └── Login.tsx
│   │   │
│   │   ├── hooks/
│   │   │   ├── useAuth.ts
│   │   │   ├── useDocuments.ts
│   │   │   ├── useQuery.ts
│   │   │   └── useUsage.ts
│   │   │
│   │   ├── lib/
│   │   │   ├── api.ts               # API client
│   │   │   ├── supabase.ts          # Supabase client
│   │   │   └── utils.ts
│   │   │
│   │   ├── types/
│   │   │   ├── api.ts
│   │   │   ├── document.ts
│   │   │   └── workspace.ts
│   │   │
│   │   └── styles/
│   │       └── globals.css
│   │
│   └── tests/
│
├── scripts/                         # Utility scripts
│   ├── setup-db.sh
│   ├── seed-data.py
│   ├── backup.sh
│   └── deploy.sh
│
└── infrastructure/                  # Optional: IaC
    ├── terraform/
    └── k8s/
```

### Key Design Decisions

#### Monorepo vs Multi-repo
**Choice**: Monorepo (all in one repository)

**Rationale**:
- Simplified versioning (single source of truth)
- Easier code sharing between server/worker
- Atomic commits across frontend/backend
- Simplified CI/CD

#### Server-Worker Code Sharing
```
worker/shared/ → symlink to server/app/
```
Workers import from shared codebase to avoid duplication of:
- Database models
- Configuration
- Core business logic (chunking, embeddings)

#### Environment-Specific Configs
- `.env.example` - Template for all env vars
- `.env.local` - Development (gitignored)
- `.env.production` - Production secrets (not in repo)

---

## 2) Docker Setup

### Docker Compose Architecture

```yaml
# docker-compose.yml
version: '3.8'

services:
  # PostgreSQL (local dev only - production uses Supabase)
  postgres:
    image: pgvector/pgvector:pg16
    container_name: rag-postgres
    environment:
      POSTGRES_DB: enterprise_rag
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  # Redis (queue backend + cache)
  redis:
    image: redis:7-alpine
    container_name: rag-redis
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  # FastAPI Server
  server:
    build:
      context: ./server
      dockerfile: Dockerfile
    container_name: rag-server
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/enterprise_rag
      - REDIS_URL=redis://redis:6379/0
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_KEY=${SUPABASE_KEY}
      - SUPABASE_JWT_SECRET=${SUPABASE_JWT_SECRET}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ENVIRONMENT=development
    volumes:
      - ./server:/app
      - /app/__pycache__
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  # RQ Workers (Extract)
  worker-extract:
    build:
      context: ./worker
      dockerfile: Dockerfile
    container_name: rag-worker-extract
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/enterprise_rag
      - REDIS_URL=redis://redis:6379/0
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_KEY=${SUPABASE_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - QUEUE_NAME=ingest_extract
      - WORKER_COUNT=5
    volumes:
      - ./worker:/app
      - ./server/app:/app/shared  # Share code
    depends_on:
      - redis
      - postgres
    deploy:
      replicas: 5
    command: python worker.py ingest_extract

  # RQ Workers (Index)
  worker-index:
    build:
      context: ./worker
      dockerfile: Dockerfile
    container_name: rag-worker-index
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@postgres:5432/enterprise_rag
      - REDIS_URL=redis://redis:6379/0
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_KEY=${SUPABASE_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - QUEUE_NAME=ingest_index
      - WORKER_COUNT=3
    volumes:
      - ./worker:/app
      - ./server/app:/app/shared
    depends_on:
      - redis
      - postgres
    deploy:
      replicas: 3
    command: python worker.py ingest_index

  # RQ Dashboard (monitoring)
  rq-dashboard:
    image: eoranged/rq-dashboard
    container_name: rag-rq-dashboard
    environment:
      - RQ_DASHBOARD_REDIS_URL=redis://redis:6379/0
    ports:
      - "9181:9181"
    depends_on:
      - redis

  # React Client
  client:
    build:
      context: ./client
      dockerfile: Dockerfile
      target: development
    container_name: rag-client
    environment:
      - VITE_API_URL=http://localhost:8000
      - VITE_SUPABASE_URL=${SUPABASE_URL}
      - VITE_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
    volumes:
      - ./client:/app
      - /app/node_modules
    ports:
      - "5173:5173"
    command: npm run dev -- --host 0.0.0.0

volumes:
  postgres_data:
  redis_data:
```

### Server Dockerfile

```dockerfile
# server/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Default command (can be overridden)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Server requirements.txt

```txt
# server/requirements.txt

# FastAPI
fastapi==0.109.0
uvicorn[standard]==0.27.0
pydantic==2.5.3
pydantic-settings==2.1.0

# Database
sqlalchemy==2.0.25
psycopg2-binary==2.9.9
alembic==1.13.1
pgvector==0.2.4

# Redis & Queue
redis==5.0.1
rq==1.16.0

# Supabase
supabase==2.3.2
storage3==0.7.4

# OpenAI
openai==1.10.0

# PDF Processing
unstructured==0.12.0
pdf2image==1.17.0
pillow==10.2.0

# Auth
python-jose[cryptography]==3.3.0
python-multipart==0.0.6

# Text Processing
tiktoken==0.5.2

# Utilities
python-dotenv==1.0.0
httpx==0.26.0
tenacity==8.2.3

# Monitoring
prometheus-client==0.19.0

# Development
pytest==7.4.4
pytest-asyncio==0.23.3
pytest-cov==4.1.0
black==24.1.1
ruff==0.1.14
mypy==1.8.0
```

### Worker Dockerfile

```dockerfile
# worker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (same as server)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 worker && chown -R worker:worker /app
USER worker

# Default command (queue name passed as arg)
CMD ["python", "worker.py"]
```

### Worker requirements.txt

```txt
# worker/requirements.txt

# Same as server/requirements.txt (workers share dependencies)
# Alternatively, use shared requirements.txt at root
-r ../server/requirements.txt
```

### Client Dockerfile

```dockerfile
# client/Dockerfile

# Multi-stage build
FROM node:20-alpine AS base

WORKDIR /app

COPY package*.json ./

# Development stage
FROM base AS development
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# Build stage
FROM base AS build
RUN npm ci
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### Client package.json

```json
{
  "name": "enterprise-rag-client",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx",
    "test": "vitest"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.21.3",
    "@supabase/supabase-js": "^2.39.3",
    "@tanstack/react-query": "^5.17.19",
    "axios": "^1.6.5",
    "zustand": "^4.5.0",
    "lucide-react": "^0.309.0",
    "clsx": "^2.1.0",
    "tailwindcss": "^3.4.1"
  },
  "devDependencies": {
    "@types/react": "^18.2.48",
    "@types/react-dom": "^18.2.18",
    "@vitejs/plugin-react": "^4.2.1",
    "typescript": "^5.3.3",
    "vite": "^5.0.11",
    "vitest": "^1.2.0",
    "eslint": "^8.56.0",
    "autoprefixer": "^10.4.17",
    "postcss": "^8.4.33"
  }
}
```

### Environment Variables

```bash
# .env.example

# ===== Supabase (Required) =====
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_JWT_SECRET=your-jwt-secret

# ===== OpenAI (Required) =====
OPENAI_API_KEY=sk-...

# ===== Database (Local Dev) =====
# Production uses Supabase Postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/enterprise_rag

# ===== Redis (Required) =====
REDIS_URL=redis://localhost:6379/0

# ===== Application Config =====
ENVIRONMENT=development
LOG_LEVEL=INFO
API_HOST=0.0.0.0
API_PORT=8000

# ===== Rate Limiting =====
RATE_LIMIT_ENABLED=true

# ===== Token Budget =====
DAILY_TOKEN_LIMIT=100000

# ===== Client (React) =====
VITE_API_URL=http://localhost:8000
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
```

### Development Commands

```bash
# Start all services
docker-compose up

# Start specific services
docker-compose up server client

# Rebuild after code changes
docker-compose up --build

# View logs
docker-compose logs -f server
docker-compose logs -f worker-extract

# Run migrations
docker-compose exec server alembic upgrade head

# Access server shell
docker-compose exec server bash

# Stop all services
docker-compose down

# Clean everything (including volumes)
docker-compose down -v
```

### Production Deployment (docker-compose.prod.yml)

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  server:
    build:
      context: ./server
      dockerfile: Dockerfile
    environment:
      - DATABASE_URL=${DATABASE_URL}  # Supabase Postgres
      - REDIS_URL=${REDIS_URL}        # Managed Redis
      - ENVIRONMENT=production
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '2'
          memory: 4G
    restart: always

  worker-extract:
    build:
      context: ./worker
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - QUEUE_NAME=ingest_extract
    deploy:
      replicas: 5
      resources:
        limits:
          cpus: '1'
          memory: 2G
    restart: always

  worker-index:
    build:
      context: ./worker
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - QUEUE_NAME=ingest_index
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '1'
          memory: 2G
    restart: always

  client:
    build:
      context: ./client
      target: production
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./ssl:/etc/nginx/ssl:ro
    restart: always
```

### CI/CD Pipeline (.github/workflows/deploy.yml)

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Run server tests
        run: |
          cd server
          docker build -t server-test .
          docker run server-test pytest
      
      - name: Run client tests
        run: |
          cd client
          npm ci
          npm test

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Deploy to production
        run: |
          docker-compose -f docker-compose.prod.yml build
          docker-compose -f docker-compose.prod.yml up -d
```

---

## 3) Core Principles

### Design Philosophy
- **Strict grounded RAG**: LLM answers ONLY from retrieved context
- **Progressive availability**: Each document becomes searchable immediately after indexing
- **Cost control**: Hard daily token budget with atomic enforcement
- **Workspace isolation**: Every operation scoped to `workspace_id`
- **Fail-safe defaults**: Reject on uncertainty, never truncate silently

### Out of Scope (v1)
- ❌ OCR for scanned PDFs
- ❌ Document summaries
- ❌ Batch orchestrator tables
- ❌ Multi-user workspaces (no roles/invites)
- ❌ Custom embedding models

---

## 4) Platform Stack

### Supabase (Managed Backend)
- **PostgreSQL** with pgvector extension
- **Storage** for PDF blobs
- **Auth** for JWT-based authentication

### Application Layer
- **FastAPI** (API server)
- **Redis** (queue backend + cache)
- **RQ** (Redis Queue for workers)

### AI Services
- **OpenAI Embeddings**: `text-embedding-3-small` (1536 dimensions)
- **OpenAI LLM**: `gpt-4o-mini`

---

## 5) Workspace Model

### Authentication
- Users authenticate via **Supabase Auth** (JWT tokens)
- JWT includes `user_id` from `auth.users.id`

### Workspace Ownership
- Any authenticated user can create a workspace
- **Single-user workspace** in v1 (creator = owner)
- All data queries MUST filter by `workspace_id`

### Workspace Limits
- 1 workspace per user in v1 (enforced at creation)
- Can be extended in v2 with workspace switching

---

## 6) Hard Limits & Constraints

### Document Limits (Per Workspace)
| Limit | Value | Enforcement |
|-------|-------|-------------|
| Max PDFs | 100 | Reject upload-prepare if exceeded |
| Max pages per PDF | 10 | Reject on upload-complete validation |
| Max file size | 20 MB | Reject on upload-prepare and re-validate on complete |

### Query Limits (Per Request)
| Limit | Value | Enforcement |
|-------|-------|-------------|
| Max documents per query | 10 | Reject if `document_ids` array > 10 |
| Max total pages in query | 50 | Sum pages across selected docs, reject if > 50 |
| Max query text length | 500 chars | Reject with validation error |

### Rate Limits (Per Workspace)
| Operation | Limit | Window |
|-----------|-------|--------|
| Queries | 100 requests | 1 minute |
| Upload-prepare | 10 requests | 1 minute |
| Upload-complete | 20 requests | 1 minute |

### Content Requirements
- **Text-based PDFs only** (no OCR in v1)
- UI must communicate: "Scanned documents not supported"
- Extraction failures → `status = failed`

---

## 7) Token Budget System

### Daily Limit
- **100,000 tokens per workspace per UTC day**
- Resets at `00:00:00 UTC`

### Token Accounting
Budget includes ALL token usage:
1. **Embedding tokens** (ingestion + query)
2. **LLM input tokens** (context + prompt)
3. **LLM output tokens** (generated answer)

### Token Estimation Rules

#### Query Embedding
```python
estimated_tokens = (len(query_text) / 4) * 1.3  # Conservative estimate
```

#### LLM Input
```python
estimated_input = (
    sum(chunk.token_count for chunk in retrieved_chunks) +
    PROMPT_TEMPLATE_TOKENS +  # ~200 tokens for system prompt
    (len(query_text) / 4)
)
```

#### LLM Output
```python
MAX_OUTPUT_TOKENS = 2000  # Hard cap configured in API call
```

#### Total Reservation
```python
total_reservation = (
    estimated_query_embedding +
    estimated_input +
    MAX_OUTPUT_TOKENS
)
```

### Reservation Model (LLM Calls)

**Before LLM call:**
```sql
-- 1. Acquire row lock and check budget
BEGIN;
SELECT tokens_used, tokens_reserved 
FROM workspace_daily_usage 
WHERE workspace_id = ? AND date = CURRENT_DATE 
FOR UPDATE;

-- 2. Check if reservation fits
IF (tokens_used + tokens_reserved + estimated_total) > 100000 THEN
    ROLLBACK;
    RETURN error("Budget exceeded");
END IF;

-- 3. Reserve tokens
UPDATE workspace_daily_usage 
SET tokens_reserved = tokens_reserved + estimated_total,
    updated_at = NOW()
WHERE workspace_id = ? AND date = CURRENT_DATE;
COMMIT;
```

**After LLM call:**
```sql
-- 4. Release reservation and deduct actual usage
BEGIN;
UPDATE workspace_daily_usage 
SET tokens_reserved = tokens_reserved - estimated_total,
    tokens_used = tokens_used + actual_total,
    updated_at = NOW()
WHERE workspace_id = ? AND date = CURRENT_DATE;
COMMIT;
```

### Pre-check Model (Embedding Calls)

**Before document ingestion:**
```python
# Estimate total embedding tokens for all chunks
estimated_embedding_tokens = sum(
    (len(chunk.content) / 4) * 1.1 
    for chunk in document_chunks
)

# Check budget atomically
result = db.execute("""
    SELECT (tokens_used + tokens_reserved + %s) <= 100000 as fits
    FROM workspace_daily_usage
    WHERE workspace_id = %s AND date = CURRENT_DATE
""", [estimated_embedding_tokens, workspace_id])

if not result.fits:
    # Reject entire document ingestion
    update_document_status(doc_id, 'failed', 
        'Insufficient token budget for embeddings')
    return
```

**After embeddings:**
```sql
-- Deduct actual usage immediately
UPDATE workspace_daily_usage 
SET tokens_used = tokens_used + actual_embedding_tokens,
    updated_at = NOW()
WHERE workspace_id = ? AND date = CURRENT_DATE;
```

### Reserved Token Cleanup (Critical)

**Problem**: Server crashes or timeouts leave tokens reserved forever

**Solution**: Background job runs every 5 minutes
```python
# Job: cleanup_stale_reservations()
# Runs: every 5 minutes

STALE_THRESHOLD = 10 minutes

reserved_entries = db.execute("""
    SELECT workspace_id, date, tokens_reserved
    FROM workspace_daily_usage
    WHERE tokens_reserved > 0 
      AND updated_at < NOW() - INTERVAL '10 minutes'
""")

for entry in reserved_entries:
    db.execute("""
        UPDATE workspace_daily_usage
        SET tokens_reserved = 0,
            updated_at = NOW()
        WHERE workspace_id = %s AND date = %s
    """, [entry.workspace_id, entry.date])
    
    # Log for investigation
    logger.warning(f"Released stale reservation: {entry}")
```

### Budget Exceeded Response
```json
{
  "error": {
    "code": "BUDGET_EXCEEDED",
    "message": "Daily token limit reached for this workspace",
    "details": {
      "used": 98500,
      "reserved": 1500,
      "limit": 100000,
      "remaining": 0,
      "resets_at": "2024-02-14T00:00:00Z"
    }
  }
}
```

---

## 8) Chunking & Retrieval Strategy

### Document Storage
- Store extracted text per page in `document_pages` table
- Preserve original page structure for citations
- Full page text available for user context

### Chunking Rules (Page-Based)

**Constraints:**
- Chunks **NEVER cross page boundaries**
- Each page may produce 1+ chunks if content is long
- Target chunk size: 400-600 tokens (adjustable)

**Overlap Strategy:**
```python
# For chunks within the same page:
CHUNK_SIZE = 500 tokens
OVERLAP = 100 tokens

# Example: Page with 1200 tokens
# Chunk 1: tokens 0-500
# Chunk 2: tokens 400-900 (100 token overlap with chunk 1)
# Chunk 3: tokens 800-1200 (100 token overlap with chunk 2)
```

**Metadata Stored Per Chunk:**
- `page_start`: Page number where chunk starts
- `page_end`: Page number where chunk ends (same as page_start in v1)
- `chunk_index`: Sequential index within document
- `content_hash`: SHA256 for idempotency

### Retrieval Strategy

**Vector Search:**
```sql
-- Retrieve top 5 chunks using pgvector
SELECT c.id, c.content, c.page_start, c.page_end, c.document_id,
       ce.embedding <=> query_embedding AS similarity
FROM chunks c
JOIN chunk_embeddings ce ON ce.chunk_id = c.id
WHERE c.workspace_id = ?
  AND c.document_id = ANY(selected_document_ids)
ORDER BY ce.embedding <=> query_embedding
LIMIT 5;
```

**HNSW Index Configuration:**
```sql
CREATE INDEX idx_chunk_embeddings_vector 
ON chunk_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Runtime setting for queries:
SET hnsw.ef_search = 40;
```

**Context Assembly:**
1. Retrieve top 5 chunks
2. For each chunk, fetch full page text from `document_pages`
3. Present to LLM: chunk snippet + full page context
4. Display to user: answer + citations with page numbers

---

## 9) Database Schema

> **Critical**: All tables include `workspace_id` for isolation  
> All queries MUST filter by `workspace_id`

### Table: `workspaces`
```sql
CREATE TABLE workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    owner_id UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_workspaces_owner ON workspaces(owner_id);
```

### Table: `documents`
```sql
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    page_count INT,
    file_hash_sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_upload',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_file_size CHECK (file_size_bytes > 0 AND file_size_bytes <= 20971520),
    CONSTRAINT chk_page_count CHECK (page_count IS NULL OR (page_count > 0 AND page_count <= 10)),
    CONSTRAINT chk_status CHECK (status IN ('pending_upload', 'uploaded', 'indexing', 'ready', 'failed'))
);

CREATE UNIQUE INDEX idx_documents_workspace_hash 
    ON documents(workspace_id, file_hash_sha256);
CREATE INDEX idx_documents_workspace ON documents(workspace_id);
CREATE INDEX idx_documents_workspace_status ON documents(workspace_id, status);
```

**Status Flow:**
```
pending_upload → uploaded → indexing → ready
                            ↓
                          failed
```

### Table: `document_pages`
```sql
CREATE TABLE document_pages (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_page_number CHECK (page_number > 0)
);

CREATE UNIQUE INDEX idx_document_pages_doc_page 
    ON document_pages(document_id, page_number);
CREATE INDEX idx_document_pages_workspace ON document_pages(workspace_id);
CREATE INDEX idx_document_pages_document ON document_pages(document_id);
```

### Table: `chunks`
```sql
CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_start INT NOT NULL,
    page_end INT NOT NULL,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_count INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_page_range CHECK (page_start > 0 AND page_end >= page_start),
    CONSTRAINT chk_chunk_index CHECK (chunk_index >= 0),
    CONSTRAINT chk_token_count CHECK (token_count > 0)
);

CREATE UNIQUE INDEX idx_chunks_doc_index 
    ON chunks(document_id, chunk_index);
CREATE INDEX idx_chunks_workspace ON chunks(workspace_id);
CREATE INDEX idx_chunks_document ON chunks(document_id);
CREATE INDEX idx_chunks_content_hash ON chunks(content_hash);
```

### Table: `chunk_embeddings`
```sql
CREATE TABLE chunk_embeddings (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chunk_embeddings_workspace ON chunk_embeddings(workspace_id);
CREATE INDEX idx_chunk_embeddings_document ON chunk_embeddings(document_id);

-- HNSW index for vector similarity search
CREATE INDEX idx_chunk_embeddings_vector 
    ON chunk_embeddings 
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

### Table: `workspace_daily_usage`
```sql
CREATE TABLE workspace_daily_usage (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    tokens_used BIGINT NOT NULL DEFAULT 0,
    tokens_reserved BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (workspace_id, date),
    CONSTRAINT chk_tokens_non_negative CHECK (tokens_used >= 0 AND tokens_reserved >= 0)
);

CREATE INDEX idx_workspace_daily_usage_date ON workspace_daily_usage(date);
```

### Table: `query_logs`
```sql
CREATE TABLE query_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    query_text TEXT NOT NULL,
    documents_searched UUID[] NOT NULL,
    retrieved_chunk_ids UUID[] NOT NULL,
    chunk_scores FLOAT[] NOT NULL,
    answer_text TEXT,
    error_message TEXT,
    
    retrieval_latency_ms INT,
    llm_latency_ms INT,
    total_latency_ms INT NOT NULL,
    
    embedding_tokens_used INT NOT NULL,
    llm_input_tokens INT,
    llm_output_tokens INT,
    total_tokens_used INT NOT NULL,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_query_logs_workspace ON query_logs(workspace_id);
CREATE INDEX idx_query_logs_user ON query_logs(user_id);
CREATE INDEX idx_query_logs_created ON query_logs(created_at DESC);
```

---

## 10) Upload Architecture

**Goal**: Support bulk uploads (up to 100 PDFs) without API timeouts or payload size issues.

### Flow Overview
```
1. Client → POST /documents/upload-prepare
2. Server → Creates placeholders, returns signed URLs
3. Client → Uploads PDFs directly to Supabase Storage (parallel)
4. Client → POST /documents/upload-complete
5. Server → Validates, enqueues per-document jobs
```

### Endpoint 1: Prepare Upload

**Request:**
```http
POST /documents/upload-prepare
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "files": [
    {"filename": "policy.pdf", "size_bytes": 1234567},
    {"filename": "manual.pdf", "size_bytes": 987654}
  ]
}
```

**Server Logic:**
1. Extract `workspace_id` from JWT
2. Validate workspace document count + new files <= 100
3. Validate each `size_bytes` <= 20 MB
4. For each file:
   - Create `documents` row with `status = 'pending_upload'`
   - Generate signed upload URL (Supabase Storage, 1 hour expiry)
   - Use storage path: `workspaces/{workspace_id}/{document_id}.pdf`

**Response:**
```json
{
  "uploads": [
    {
      "document_id": "550e8400-e29b-41d4-a716-446655440000",
      "filename": "policy.pdf",
      "upload_url": "https://your-project.supabase.co/storage/v1/object/...",
      "storage_path": "workspaces/{workspace_id}/{document_id}.pdf",
      "expires_at": "2024-02-13T15:30:00Z"
    }
  ]
}
```

**Error Cases:**
```json
// Exceeds workspace limit
{
  "error": {
    "code": "WORKSPACE_LIMIT_EXCEEDED",
    "message": "Cannot upload 20 files. Workspace limit is 100 documents (currently 85).",
    "details": {
      "current_count": 85,
      "requested": 20,
      "limit": 100
    }
  }
}

// File too large
{
  "error": {
    "code": "FILE_TOO_LARGE",
    "message": "File 'large.pdf' exceeds 20MB limit",
    "details": {
      "filename": "large.pdf",
      "size_bytes": 25000000,
      "limit_bytes": 20971520
    }
  }
}
```

### Client Upload Phase

**Client responsibility:**
```javascript
// Upload files in parallel using signed URLs
const uploadPromises = prepareResponse.uploads.map(upload => 
  fetch(upload.upload_url, {
    method: 'PUT',
    body: pdfFile,
    headers: {'Content-Type': 'application/pdf'}
  })
);

await Promise.all(uploadPromises);
```

### Endpoint 2: Complete Upload

**Request:**
```http
POST /documents/upload-complete
Authorization: Bearer {jwt_token}
Content-Type: application/json

{
  "documents": [
    {
      "document_id": "550e8400-e29b-41d4-a716-446655440000",
      "filename": "policy.pdf",
      "file_size_bytes": 1234567,
      "page_count": 8,
      "file_hash_sha256": "a3c5e8...",
      "storage_path": "workspaces/{workspace_id}/{document_id}.pdf"
    }
  ]
}
```

**Server Logic (Per Document):**
```python
for doc_data in request.documents:
    # 1. Idempotency check - atomic status transition
    result = db.execute("""
        UPDATE documents 
        SET status = 'uploaded',
            filename = %s,
            file_size_bytes = %s,
            page_count = %s,
            file_hash_sha256 = %s,
            storage_path = %s,
            updated_at = NOW()
        WHERE id = %s 
          AND workspace_id = %s
          AND status = 'pending_upload'
        RETURNING id
    """, [doc_data.filename, doc_data.file_size_bytes, ...])
    
    if result.rowcount == 0:
        # Already processed or invalid state - skip
        failed.append({
            "document_id": doc_data.document_id,
            "reason": "Already processed or invalid state"
        })
        continue
    
    # 2. Re-validate constraints
    if doc_data.file_size_bytes > 20_971_520:
        update_status(doc_id, 'failed', 'File exceeds 20MB')
        failed.append(...)
        continue
        
    if doc_data.page_count > 10:
        update_status(doc_id, 'failed', 'Exceeds 10 page limit')
        failed.append(...)
        continue
    
    # 3. Check deduplication (workspace-scoped)
    duplicate = db.execute("""
        SELECT id, filename FROM documents
        WHERE workspace_id = %s 
          AND file_hash_sha256 = %s
          AND id != %s
          AND status = 'ready'
    """, [workspace_id, doc_data.file_hash_sha256, doc_id])
    
    if duplicate:
        # Point to existing document, delete placeholder
        update_status(doc_id, 'failed', 
            f'Duplicate of existing document: {duplicate.filename}')
        failed.append(...)
        continue
    
    # 4. Enqueue extraction job
    queue.enqueue('ingest_extract', document_id=doc_id)
    successful.append({
        "document_id": doc_id,
        "status": "uploaded"
    })
```

**Response (Partial Success Support):**
```json
{
  "successful": [
    {
      "document_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "uploaded",
      "message": "Queued for processing"
    }
  ],
  "failed": [
    {
      "document_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "reason": "Exceeds 10 page limit",
      "details": {"page_count": 15, "limit": 10}
    }
  ]
}
```

---

## 11) Ingestion Pipeline

### Queue Configuration
```python
# Queue names
QUEUE_EXTRACT = 'ingest_extract'
QUEUE_INDEX = 'ingest_index'

# Worker allocation
WORKERS = {
    'ingest_extract': 5,  # I/O bound (PDF parsing)
    'ingest_index': 3     # API rate-limit bound (OpenAI)
}

# Job settings
DEFAULT_JOB_TIMEOUT = 600  # 10 minutes
MAX_RETRIES = 3
```

### Job 1: Extract Text (`ingest_extract`)

**Input:** `document_id`

**Steps:**
```python
def ingest_extract(document_id: str):
    try:
        # 1. Load document metadata
        doc = db.get_document(document_id)
        
        # 2. Download PDF from Supabase Storage
        pdf_bytes = supabase.storage.download(doc.storage_path)
        
        # 3. Extract text using Unstructured
        from unstructured.partition.pdf import partition_pdf
        elements = partition_pdf(
            file=BytesIO(pdf_bytes),
            strategy="fast"  # No OCR
        )
        
        # 4. Group by page
        pages = {}
        for element in elements:
            page_num = element.metadata.page_number
            if page_num not in pages:
                pages[page_num] = []
            pages[page_num].append(element.text)
        
        # 5. Validate page count
        if len(pages) > 10:
            raise ValueError(f"Document has {len(pages)} pages, limit is 10")
        
        if len(pages) == 0:
            raise ValueError("No text extracted - document may be scanned/OCR required")
        
        # 6. Insert document_pages
        for page_num, texts in sorted(pages.items()):
            content = "\n".join(texts)
            db.insert_document_page(
                workspace_id=doc.workspace_id,
                document_id=document_id,
                page_number=page_num,
                content=content
            )
        
        # 7. Update document status
        db.update_document(
            document_id,
            status='indexing',
            page_count=len(pages)
        )
        
        # 8. Enqueue indexing job
        queue.enqueue(QUEUE_INDEX, 'ingest_index', document_id=document_id)
        
    except Exception as e:
        logger.error(f"Extract failed for {document_id}: {e}")
        db.update_document(
            document_id,
            status='failed',
            error_message=str(e)
        )
        raise  # Let RQ handle retry logic
```

### Job 2: Index Document (`ingest_index`)

**Input:** `document_id`

**Steps:**
```python
def ingest_index(document_id: str):
    try:
        # 1. Load document and pages
        doc = db.get_document(document_id)
        pages = db.get_document_pages(document_id)
        
        # 2. Chunk each page
        all_chunks = []
        chunk_index = 0
        
        for page in pages:
            page_chunks = chunk_text(
                text=page.content,
                max_tokens=500,
                overlap_tokens=100
            )
            
            for chunk_text in page_chunks:
                all_chunks.append({
                    'chunk_index': chunk_index,
                    'page_start': page.page_number,
                    'page_end': page.page_number,
                    'content': chunk_text,
                    'content_hash': hashlib.sha256(chunk_text.encode()).hexdigest()
                })
                chunk_index += 1
        
        # 3. Estimate embedding token cost
        estimated_tokens = sum(
            int(len(chunk['content']) / 4 * 1.1)
            for chunk in all_chunks
        )
        
        # 4. Check token budget
        usage = db.get_daily_usage(doc.workspace_id)
        if (usage.tokens_used + usage.tokens_reserved + estimated_tokens) > 100_000:
            raise BudgetExceededError(
                f"Insufficient budget for embeddings: {estimated_tokens} tokens needed"
            )
        
        # 5. Insert chunks (idempotent via content_hash)
        chunk_ids = []
        for chunk_data in all_chunks:
            chunk_id = db.upsert_chunk(
                workspace_id=doc.workspace_id,
                document_id=document_id,
                **chunk_data
            )
            chunk_ids.append(chunk_id)
        
        # 6. Generate embeddings
        texts = [c['content'] for c in all_chunks]
        embeddings = openai.embeddings.create(
            model='text-embedding-3-small',
            input=texts
        )
        
        actual_tokens = embeddings.usage.total_tokens
        
        # 7. Insert embeddings
        for chunk_id, embedding_data in zip(chunk_ids, embeddings.data):
            db.insert_chunk_embedding(
                chunk_id=chunk_id,
                workspace_id=doc.workspace_id,
                document_id=document_id,
                embedding=embedding_data.embedding,
                embedding_model='text-embedding-3-small'
            )
        
        # 8. Deduct token usage
        db.increment_token_usage(doc.workspace_id, actual_tokens)
        
        # 9. Mark document ready
        db.update_document(document_id, status='ready')
        
    except BudgetExceededError as e:
        logger.warning(f"Budget exceeded for {document_id}: {e}")
        db.update_document(
            document_id,
            status='failed',
            error_message=str(e)
        )
        # Don't retry - budget issue
        
    except Exception as e:
        logger.error(f"Index failed for {document_id}: {e}")
        db.update_document(
            document_id,
            status='failed',
            error_message=str(e)
        )
        raise  # Let RQ handle retry logic
```

---

## 12) Query System (RAG)

### Endpoint: POST /query

**Request:**
```json
{
  "question": "What is the refund policy for defective products?",
  "document_ids": [
    "550e8400-e29b-41d4-a716-446655440000",
    "7c9e6679-7425-40de-944b-e07fc1f90ae7"
  ]
}
```

**Validation:**
```python
# 1. Required fields
if not request.document_ids:
    return error("document_ids required")

# 2. Document count limit
if len(request.document_ids) > 10:
    return error("Maximum 10 documents per query")

# 3. Query length limit
if len(request.question) > 500:
    return error("Question exceeds 500 character limit")

# 4. Verify all documents are ready
docs = db.get_documents(request.document_ids, workspace_id)
not_ready = [d for d in docs if d.status != 'ready']
if not_ready:
    return error(f"Documents not ready: {[d.id for d in not_ready]}")

# 5. Check total page count
total_pages = sum(d.page_count for d in docs)
if total_pages > 50:
    return error(f"Total pages ({total_pages}) exceeds limit of 50")
```

**Processing Flow:**
```python
def process_query(question: str, document_ids: List[str], workspace_id: str):
    start_time = time.time()
    
    # === STEP 1: Generate query embedding ===
    query_embedding_start = time.time()
    
    query_embed_response = openai.embeddings.create(
        model='text-embedding-3-small',
        input=question
    )
    query_embedding = query_embed_response.data[0].embedding
    embedding_tokens = query_embed_response.usage.total_tokens
    
    retrieval_latency = int((time.time() - query_embedding_start) * 1000)
    
    # === STEP 2: Reserve tokens for LLM call ===
    PROMPT_TEMPLATE_TOKENS = 200
    MAX_OUTPUT_TOKENS = 2000
    
    # Will retrieve chunks - estimate worst case
    estimated_chunk_tokens = 5 * 600  # 5 chunks * max 600 tokens each
    estimated_input = estimated_chunk_tokens + PROMPT_TEMPLATE_TOKENS + len(question) // 4
    estimated_total = embedding_tokens + estimated_input + MAX_OUTPUT_TOKENS
    
    # Atomic reservation
    try:
        reserve_tokens(workspace_id, estimated_total)
    except BudgetExceededError as e:
        # Log query attempt even though it failed
        log_query(
            workspace_id=workspace_id,
            query_text=question,
            error_message=str(e),
            total_tokens_used=embedding_tokens
        )
        raise
    
    try:
        # === STEP 3: Retrieve chunks ===
        chunks = db.execute("""
            SELECT c.id, c.content, c.page_start, c.page_end, 
                   c.document_id, d.filename,
                   ce.embedding <=> %s::vector AS similarity
            FROM chunks c
            JOIN chunk_embeddings ce ON ce.chunk_id = c.id
            JOIN documents d ON d.id = c.document_id
            WHERE c.workspace_id = %s
              AND c.document_id = ANY(%s)
            ORDER BY ce.embedding <=> %s::vector
            LIMIT 5
        """, [query_embedding, workspace_id, document_ids, query_embedding])
        
        if not chunks:
            raise InsufficientContextError("No relevant content found in selected documents")
        
        # === STEP 4: Build LLM prompt ===
        context = build_context(chunks)
        prompt = build_grounded_prompt(question, context)
        
        # === STEP 5: Call LLM ===
        llm_start = time.time()
        
        response = openai.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.1
        )
        
        llm_latency = int((time.time() - llm_start) * 1000)
        
        answer = response.choices[0].message.content
        actual_input_tokens = response.usage.prompt_tokens
        actual_output_tokens = response.usage.completion_tokens
        actual_total = actual_input_tokens + actual_output_tokens
        
        # === STEP 6: Release reservation, deduct actual usage ===
        release_reservation_and_charge(
            workspace_id, 
            reserved=estimated_total,
            actual=embedding_tokens + actual_total
        )
        
        # === STEP 7: Extract citations ===
        citations = extract_citations(answer, chunks)
        
        # === STEP 8: Log query ===
        total_latency = int((time.time() - start_time) * 1000)
        log_query(
            workspace_id=workspace_id,
            user_id=current_user_id,
            query_text=question,
            documents_searched=document_ids,
            retrieved_chunk_ids=[c.id for c in chunks],
            chunk_scores=[c.similarity for c in chunks],
            answer_text=answer,
            retrieval_latency_ms=retrieval_latency,
            llm_latency_ms=llm_latency,
            total_latency_ms=total_latency,
            embedding_tokens_used=embedding_tokens,
            llm_input_tokens=actual_input_tokens,
            llm_output_tokens=actual_output_tokens,
            total_tokens_used=embedding_tokens + actual_total
        )
        
        return {
            "answer": answer,
            "citations": citations,
            "token_usage": {
                "embedding": embedding_tokens,
                "input": actual_input_tokens,
                "output": actual_output_tokens,
                "total": embedding_tokens + actual_total
            }
        }
        
    except Exception as e:
        # Release reservation on any error
        release_reservation(workspace_id, estimated_total)
        raise
```

### System Prompt (Strict Grounding)
```python
SYSTEM_PROMPT = """You are a helpful assistant that answers questions based ONLY on the provided document context.

CRITICAL RULES:
1. Answer ONLY using information from the context provided
2. If the context does not contain enough information, respond: "The provided documents do not contain sufficient information to answer this question."
3. ALWAYS cite your sources using this format: [Document: {filename}, Page {page_number}]
4. Include direct quotes when possible to support your answer
5. Do NOT use external knowledge or make assumptions
6. Do NOT speculate or infer beyond what is explicitly stated

Your response should be:
- Accurate and grounded in the context
- Properly cited with document and page references
- Clear about limitations when information is insufficient
"""
```

### User Prompt Template
```python
def build_grounded_prompt(question: str, chunks: List[Chunk]) -> str:
    context_parts = []
    
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(f"""
[Document: {chunk.filename}, Page {chunk.page_start}]
{chunk.content}
""")
    
    context = "\n\n".join(context_parts)
    
    return f"""Context from documents:
{context}

Question: {question}

Answer (cite sources):"""
```

### Response Format
```json
{
  "answer": "According to the refund policy, defective products can be returned within 30 days of purchase for a full refund. The product must be in its original packaging with proof of purchase.",
  "citations": [
    {
      "document_name": "refund_policy.pdf",
      "page": 3,
      "snippet": "Defective products can be returned within 30 days of purchase for a full refund"
    }
  ],
  "token_usage": {
    "embedding": 45,
    "input": 1250,
    "output": 87,
    "total": 1382
  }
}
```

### Insufficient Context Response
```json
{
  "answer": "The provided documents do not contain sufficient information to answer this question about extended warranty coverage.",
  "citations": [],
  "token_usage": {
    "embedding": 42,
    "input": 850,
    "output": 35,
    "total": 927
  }
}
```

---

## 13) API Endpoints

### Authentication
All endpoints require:
```http
Authorization: Bearer {supabase_jwt_token}
```

JWT payload must include:
- `sub`: user_id
- `workspace_id`: extracted from user's workspace membership

### Workspace Management

#### Create Workspace
```http
POST /workspaces
Content-Type: application/json

{
  "name": "My Company Workspace"
}
```

**Response (201 Created):**
```json
{
  "workspace_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "My Company Workspace",
  "owner_id": "user_uuid",
  "created_at": "2024-02-13T10:30:00Z"
}
```

#### Get Current Workspace
```http
GET /workspaces/me
```

**Response (200 OK):**
```json
{
  "workspace": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "My Company Workspace",
    "owner_id": "user_uuid",
    "created_at": "2024-02-13T10:30:00Z"
  },
  "stats": {
    "document_count": 42,
    "document_limit": 100,
    "documents_by_status": {
      "ready": 38,
      "indexing": 3,
      "failed": 1
    }
  },
  "usage_today": {
    "tokens_used": 45200,
    "tokens_reserved": 800,
    "limit": 100000,
    "remaining": 54000,
    "resets_at": "2024-02-14T00:00:00Z"
  }
}
```

### Document Management

#### Prepare Upload
```http
POST /documents/upload-prepare
Content-Type: application/json

{
  "files": [
    {"filename": "policy.pdf", "size_bytes": 1234567}
  ]
}
```

**Response (200 OK):**
```json
{
  "uploads": [
    {
      "document_id": "doc_uuid",
      "filename": "policy.pdf",
      "upload_url": "https://...",
      "storage_path": "workspaces/{workspace_id}/{document_id}.pdf",
      "expires_at": "2024-02-13T15:30:00Z"
    }
  ]
}
```

#### Complete Upload
```http
POST /documents/upload-complete
Content-Type: application/json

{
  "documents": [
    {
      "document_id": "doc_uuid",
      "filename": "policy.pdf",
      "file_size_bytes": 1234567,
      "page_count": 8,
      "file_hash_sha256": "a3c5e8...",
      "storage_path": "workspaces/{workspace_id}/{document_id}.pdf"
    }
  ]
}
```

**Response (200 OK):**
```json
{
  "successful": [
    {
      "document_id": "doc_uuid",
      "status": "uploaded",
      "message": "Queued for processing"
    }
  ],
  "failed": []
}
```

#### List Documents
```http
GET /documents?page=1&page_size=20&status=ready
```

**Response (200 OK):**
```json
{
  "documents": [
    {
      "id": "doc_uuid",
      "filename": "policy.pdf",
      "file_size_bytes": 1234567,
      "page_count": 8,
      "status": "ready",
      "created_at": "2024-02-13T10:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total_count": 42,
    "total_pages": 3
  }
}
```

#### Get Document
```http
GET /documents/{document_id}
```

**Response (200 OK):**
```json
{
  "id": "doc_uuid",
  "filename": "policy.pdf",
  "file_size_bytes": 1234567,
  "page_count": 8,
  "file_hash_sha256": "a3c5e8...",
  "status": "ready",
  "error_message": null,
  "created_at": "2024-02-13T10:00:00Z",
  "updated_at": "2024-02-13T10:05:00Z"
}
```

#### Delete Document
```http
DELETE /documents/{document_id}
```

**Response (204 No Content)**

**Server Logic:**
```python
# Cascade delete (handled by FK constraints):
# - document_pages
# - chunks
# - chunk_embeddings
# Delete storage object
supabase.storage.delete(doc.storage_path)

# Delete document row
db.delete_document(document_id)
```

### Query

#### Ask Question
```http
POST /query
Content-Type: application/json

{
  "question": "What is the refund policy?",
  "document_ids": ["doc_uuid_1", "doc_uuid_2"]
}
```

**Response (200 OK):**
```json
{
  "answer": "...",
  "citations": [...],
  "token_usage": {
    "embedding": 45,
    "input": 1250,
    "output": 87,
    "total": 1382
  }
}
```

### Usage

#### Get Today's Usage
```http
GET /usage/today
```

**Response (200 OK):**
```json
{
  "date": "2024-02-13",
  "tokens_used": 45200,
  "tokens_reserved": 800,
  "limit": 100000,
  "remaining": 54000,
  "resets_at": "2024-02-14T00:00:00Z"
}
```

#### Get Usage Breakdown
```http
GET /usage/breakdown?days=7
```

**Response (200 OK):**
```json
{
  "period": {
    "start": "2024-02-07",
    "end": "2024-02-13"
  },
  "by_date": [
    {
      "date": "2024-02-13",
      "tokens_used": 45200,
      "queries": 128,
      "documents_indexed": 5
    }
  ],
  "by_operation": {
    "embeddings_ingestion": 12500,
    "embeddings_query": 5800,
    "llm_input": 18900,
    "llm_output": 8000
  },
  "top_documents": [
    {
      "document_id": "doc_uuid",
      "filename": "policy.pdf",
      "tokens_used": 8500
    }
  ]
}
```

---

## 14) Worker Configuration

### Queue Setup (Redis)
```python
from redis import Redis
from rq import Queue

redis_conn = Redis(
    host=os.getenv('REDIS_HOST'),
    port=6379,
    db=0,
    decode_responses=False
)

queue_extract = Queue('ingest_extract', connection=redis_conn)
queue_index = Queue('ingest_index', connection=redis_conn)
```

### Worker Processes
```bash
# Start extract workers (5 processes)
rq worker ingest_extract --burst &
rq worker ingest_extract --burst &
rq worker ingest_extract --burst &
rq worker ingest_extract --burst &
rq worker ingest_extract --burst &

# Start index workers (3 processes)
rq worker ingest_index --burst &
rq worker ingest_index --burst &
rq worker ingest_index --burst &
```

### Worker Configuration
```python
# worker_config.py
WORKER_CONFIG = {
    'ingest_extract': {
        'count': 5,
        'timeout': 600,  # 10 minutes
        'max_retries': 3,
        'retry_delays': [10, 60, 300]  # 10s, 1m, 5m
    },
    'ingest_index': {
        'count': 3,
        'timeout': 600,
        'max_retries': 3,
        'retry_delays': [10, 60, 300]
    }
}
```

### Job Monitoring
```python
# Get queue status
def get_queue_status(queue_name: str) -> dict:
    queue = Queue(queue_name, connection=redis_conn)
    
    return {
        'name': queue_name,
        'queued': queue.count,
        'started': len(queue.started_job_registry),
        'finished': len(queue.finished_job_registry),
        'failed': len(queue.failed_job_registry),
        'deferred': len(queue.deferred_job_registry)
    }
```

---

## 15) Error Handling & Retries

### Retry Policy

#### Transient Errors (Retry)
- Network timeouts
- OpenAI rate limits (429)
- Supabase temporary unavailability
- Redis connection errors

**Strategy:**
```python
MAX_RETRIES = 3
RETRY_DELAYS = [10, 60, 300]  # seconds: 10s, 1m, 5m

# Exponential backoff with jitter
import random
def get_retry_delay(attempt: int) -> int:
    base_delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
    jitter = random.uniform(0, base_delay * 0.1)
    return base_delay + jitter
```

#### Permanent Errors (No Retry)
- Invalid PDF format
- Document exceeds limits (pages, size)
- Budget exceeded (for ingestion)
- Extraction yields no text

**Handling:**
```python
class PermanentError(Exception):
    """Errors that should not be retried"""
    pass

class BudgetExceededError(PermanentError):
    pass

class InvalidDocumentError(PermanentError):
    pass

# In worker job
try:
    process_document()
except PermanentError as e:
    # Mark as failed immediately, don't retry
    update_status('failed', str(e))
    return  # Don't raise - prevents RQ retry
except Exception as e:
    # Let RQ handle retry
    raise
```

### Error Messages (User-Facing)

**Document Processing Errors:**
```python
ERROR_MESSAGES = {
    'page_limit': 'Document exceeds 10 page limit (found {page_count} pages)',
    'extraction_failed': 'Unable to extract text. Ensure PDF contains selectable text (not scanned images)',
    'budget_exceeded': 'Insufficient token budget to index this document. Estimated: {estimated} tokens, Available: {available}',
    'invalid_format': 'Invalid PDF format or corrupted file',
    'timeout': 'Processing timed out after multiple retries. Please try re-uploading.',
}
```

### Dead Letter Queue
```python
# After max retries, move to DLQ for manual investigation
def handle_max_retries_exceeded(job):
    dlq = Queue('dead_letter_queue', connection=redis_conn)
    dlq.enqueue('log_failed_job', job_id=job.id, error=job.exc_info)
    
    # Update document status
    db.update_document(
        job.kwargs['document_id'],
        status='failed',
        error_message='Max retries exceeded. Support has been notified.'
    )
```

---

## 16) Monitoring & Health Checks

### Health Check Endpoint
```http
GET /health
```

**Response (200 OK):**
```json
{
  "status": "healthy",
  "timestamp": "2024-02-13T10:30:00Z",
  "components": {
    "database": {
      "status": "healthy",
      "latency_ms": 12
    },
    "redis": {
      "status": "healthy",
      "latency_ms": 3
    },
    "storage": {
      "status": "healthy",
      "latency_ms": 45
    },
    "workers": {
      "ingest_extract": {
        "status": "healthy",
        "queued": 15,
        "processing": 3,
        "failed_last_hour": 2
      },
      "ingest_index": {
        "status": "warning",
        "queued": 42,
        "processing": 3,
        "failed_last_hour": 8
      }
    }
  }
}
```

### Metrics to Track

#### Queue Metrics
```python
# Monitor every minute
metrics = {
    'queue_depth': queue.count,
    'jobs_processing': len(queue.started_job_registry),
    'jobs_failed_1h': count_failed_last_hour(),
    'avg_job_duration': calculate_avg_duration(),
}

# Alerts
if metrics['queue_depth'] > 100:
    alert('Queue depth high', severity='warning')

if metrics['jobs_failed_1h'] > 10:
    alert('High failure rate', severity='critical')
```

#### Token Usage Metrics
```python
# Track per workspace
metrics = {
    'tokens_used_today': get_usage(workspace_id),
    'tokens_reserved': get_reserved(workspace_id),
    'percent_used': (tokens_used / 100000) * 100,
}

# Alerts (per workspace)
if metrics['percent_used'] > 90:
    notify_workspace_owner('Approaching daily token limit')
```

#### Worker Health
```python
# Check worker heartbeat
def check_worker_health():
    workers = Worker.all(connection=redis_conn)
    
    for worker in workers:
        last_heartbeat = worker.last_heartbeat
        
        if (datetime.now() - last_heartbeat).seconds > 300:
            alert(f'Worker {worker.name} not responding', severity='critical')
```

### Alerting Rules

| Condition | Severity | Action |
|-----------|----------|--------|
| Queue depth > 100 for 5+ min | Warning | Scale workers |
| Failed jobs > 10/hour | Critical | Investigate |
| Worker no heartbeat 5+ min | Critical | Restart worker |
| Workspace at 90% budget | Info | Notify user |
| Workspace at 100% budget | Warning | Notify user |
| Database latency > 500ms | Warning | Check DB load |

---

## 17) Security & Rate Limiting

### Authentication
```python
from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError

async def get_current_user(authorization: str = Header(...)):
    """Extract user_id from Supabase JWT"""
    try:
        token = authorization.replace('Bearer ', '')
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=['HS256']
        )
        user_id = payload['sub']
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail='Invalid token')

async def get_workspace_id(user_id: str = Depends(get_current_user)):
    """Get user's workspace"""
    workspace = db.get_user_workspace(user_id)
    if not workspace:
        raise HTTPException(status_code=404, detail='No workspace found')
    return workspace.id
```

### Rate Limiting (Redis)
```python
from fastapi import Request
from redis import Redis

redis_client = Redis(...)

RATE_LIMITS = {
    'query': (100, 60),           # 100 requests per 60 seconds
    'upload-prepare': (10, 60),   # 10 requests per 60 seconds
    'upload-complete': (20, 60),  # 20 requests per 60 seconds
}

async def rate_limit(request: Request, operation: str, workspace_id: str):
    limit, window = RATE_LIMITS[operation]
    key = f'ratelimit:{workspace_id}:{operation}'
    
    current = redis_client.incr(key)
    
    if current == 1:
        redis_client.expire(key, window)
    
    if current > limit:
        raise HTTPException(
            status_code=429,
            detail=f'Rate limit exceeded. Max {limit} requests per {window}s',
            headers={'Retry-After': str(window)}
        )
```

### Workspace Isolation (Critical)
```python
# EVERY database query MUST include workspace_id filter
# BAD - Vulnerable to unauthorized access
def get_document(document_id: str):
    return db.query("SELECT * FROM documents WHERE id = ?", [document_id])

# GOOD - Enforces workspace isolation
def get_document(document_id: str, workspace_id: str):
    return db.query("""
        SELECT * FROM documents 
        WHERE id = ? AND workspace_id = ?
    """, [document_id, workspace_id])
```

### Input Validation
```python
from pydantic import BaseModel, Field, validator

class QueryRequest(BaseModel):
    question: str = Field(..., max_length=500)
    document_ids: List[str] = Field(..., min_items=1, max_items=10)
    
    @validator('question')
    def question_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Question cannot be empty')
        return v
    
    @validator('document_ids')
    def valid_uuids(cls, v):
        for doc_id in v:
            try:
                uuid.UUID(doc_id)
            except ValueError:
                raise ValueError(f'Invalid document ID: {doc_id}')
        return v
```

---

## 18) Maintenance & Operations

### Background Jobs

#### Daily Token Reset
```python
# Runs at 00:00:05 UTC daily
def reset_daily_budgets():
    """Reset all workspace token budgets"""
    db.execute("""
        INSERT INTO workspace_daily_usage (workspace_id, date, tokens_used, tokens_reserved)
        SELECT id, CURRENT_DATE, 0, 0
        FROM workspaces
        ON CONFLICT (workspace_id, date) DO NOTHING
    """)
```

#### Cleanup Old Query Logs
```python
# Runs daily at 02:00 UTC
def cleanup_old_logs():
    """Delete query logs older than 90 days"""
    deleted = db.execute("""
        DELETE FROM query_logs
        WHERE created_at < NOW() - INTERVAL '90 days'
    """)
    logger.info(f"Deleted {deleted.rowcount} old query logs")
```

#### Release Stale Reservations
```python
# Runs every 5 minutes
def cleanup_stale_reservations():
    """Release token reservations older than 10 minutes"""
    db.execute("""
        UPDATE workspace_daily_usage
        SET tokens_reserved = 0,
            updated_at = NOW()
        WHERE tokens_reserved > 0
          AND updated_at < NOW() - INTERVAL '10 minutes'
    """)
```

#### Reindex Embeddings (Manual)
```python
# For maintenance/upgrades
def reindex_embeddings_batch(batch_size=100):
    """Reindex with HNSW optimization"""
    db.execute("VACUUM ANALYZE chunk_embeddings")
    db.execute("""
        REINDEX INDEX CONCURRENTLY idx_chunk_embeddings_vector
    """)
```

### Database Maintenance
```sql
-- Run weekly
VACUUM ANALYZE documents;
VACUUM ANALYZE chunks;
VACUUM ANALYZE chunk_embeddings;
VACUUM ANALYZE workspace_daily_usage;

-- Monitor index bloat
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY pg_relation_size(indexrelid) DESC;
```

### Backup Strategy
```bash
# Daily automated backups (Supabase handles this)
# Point-in-time recovery available

# Manual backup for critical migrations
pg_dump -h supabase-host -U postgres -d database_name > backup.sql

# Backup storage bucket
supabase storage backup --bucket document-storage
```

### Deployment Checklist

**Pre-deployment:**
- [ ] Run database migrations
- [ ] Update environment variables
- [ ] Test worker connectivity
- [ ] Verify OpenAI API keys
- [ ] Check Supabase quotas

**Post-deployment:**
- [ ] Verify health endpoint
- [ ] Check worker queue status
- [ ] Test end-to-end upload flow
- [ ] Monitor error rates (15 min)
- [ ] Verify token budget tracking

---

## Appendix A: Token Estimation Reference

### Embedding Tokens
```python
# text-embedding-3-small
def estimate_embedding_tokens(text: str) -> int:
    # Conservative estimate: 1 token per 4 characters, +10% overhead
    return int(len(text) / 4 * 1.1)

# Example
text = "This is a sample chunk of text from a PDF document."
tokens = estimate_embedding_tokens(text)  # ~15 tokens
```

### LLM Tokens
```python
# gpt-4o-mini
def estimate_llm_tokens(text: str) -> int:
    # More accurate: use tiktoken
    import tiktoken
    encoder = tiktoken.encoding_for_model('gpt-4o-mini')
    return len(encoder.encode(text))

# Conservative fallback
def estimate_llm_tokens_fallback(text: str) -> int:
    return int(len(text) / 4)
```

---

## Appendix B: Example Queries

### Create Workspace
```bash
curl -X POST https://api.example.com/workspaces \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Workspace"}'
```

### Upload Documents
```bash
# 1. Prepare
response=$(curl -X POST https://api.example.com/documents/upload-prepare \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"files": [{"filename": "doc.pdf", "size_bytes": 123456}]}')

upload_url=$(echo $response | jq -r '.uploads[0].upload_url')
doc_id=$(echo $response | jq -r '.uploads[0].document_id')

# 2. Upload to Supabase
curl -X PUT "$upload_url" \
  -H "Content-Type: application/pdf" \
  --data-binary @doc.pdf

# 3. Complete
curl -X POST https://api.example.com/documents/upload-complete \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"documents\": [{\"document_id\": \"$doc_id\", ...}]}"
```

### Query
```bash
curl -X POST https://api.example.com/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the refund policy?",
    "document_ids": ["doc-uuid-1", "doc-uuid-2"]
  }'
```

---

## Appendix C: Migration Path (Future)

### v1 → v2 Potential Enhancements
- Multi-user workspaces with roles
- OCR support for scanned PDFs
- Document summaries
- Batch upload orchestrator
- Custom embedding models
- Hybrid search (keyword + semantic)
- Document versioning
- Export to various formats

**Migration Considerations:**
- Add `embedding_version` to support model upgrades
- Add `processing_version` to track chunking strategy changes
- Implement background re-processing jobs for upgrades

---

**END OF SPECIFICATION**

This architecture is locked for v1 implementation. Any changes require architectural review and version increment.
