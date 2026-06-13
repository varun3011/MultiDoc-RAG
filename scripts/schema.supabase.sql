BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    owner_id UUID NOT NULL REFERENCES auth.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_id);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'preparing',
    total_documents INT NOT NULL DEFAULT 0,
    accepted_documents INT NOT NULL DEFAULT 0,
    rejected_documents INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_ingestion_runs_status CHECK (status IN ('preparing', 'processing', 'completed', 'partial', 'failed')),
    CONSTRAINT chk_ingestion_runs_counts CHECK (
        total_documents >= 0
        AND accepted_documents >= 0
        AND rejected_documents >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_workspace_created
    ON ingestion_runs(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_workspace_status
    ON ingestion_runs(workspace_id, status);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    page_count INT,
    file_hash_sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    ingestion_run_id UUID,
    status TEXT NOT NULL DEFAULT 'pending_upload',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    upload_completed_at TIMESTAMPTZ,
    extract_enqueued_at TIMESTAMPTZ,
    extract_started_at TIMESTAMPTZ,
    extract_finished_at TIMESTAMPTZ,
    index_enqueued_at TIMESTAMPTZ,
    index_started_at TIMESTAMPTZ,
    index_finished_at TIMESTAMPTZ,

    CONSTRAINT chk_file_size CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760),
    CONSTRAINT chk_page_count CHECK (page_count IS NULL OR (page_count > 0 AND page_count <= 10)),
    CONSTRAINT chk_status CHECK (status IN ('pending_upload', 'uploading', 'uploaded', 'extracting', 'indexing', 'indexed', 'ready', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_workspace_hash
    ON documents(workspace_id, file_hash_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents(workspace_id);
CREATE INDEX IF NOT EXISTS idx_documents_workspace_status ON documents(workspace_id, status);

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS ingestion_run_id UUID;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS upload_completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS extract_enqueued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS extract_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS extract_finished_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS index_enqueued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS index_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS index_finished_at TIMESTAMPTZ;

ALTER TABLE documents
    DROP CONSTRAINT IF EXISTS chk_status;

ALTER TABLE documents
    ADD CONSTRAINT chk_status
    CHECK (status IN ('pending_upload', 'uploading', 'uploaded', 'extracting', 'indexing', 'indexed', 'ready', 'failed'));

DO $$
BEGIN
    ALTER TABLE documents
        ADD CONSTRAINT fk_documents_ingestion_run
        FOREIGN KEY (ingestion_run_id)
        REFERENCES ingestion_runs(id)
        ON DELETE SET NULL;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_documents_ingestion_run
    ON documents(workspace_id, ingestion_run_id);

CREATE TABLE IF NOT EXISTS document_pages (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_page_number CHECK (page_number > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_document_pages_doc_page
    ON document_pages(document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_document_pages_workspace ON document_pages(workspace_id);
CREATE INDEX IF NOT EXISTS idx_document_pages_document ON document_pages(document_id);

CREATE TABLE IF NOT EXISTS chunks (
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_doc_index
    ON chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON chunks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_workspace ON chunk_embeddings(workspace_id);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_document ON chunk_embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector
    ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS workspace_daily_usage (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    tokens_used BIGINT NOT NULL DEFAULT 0,
    tokens_reserved BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (workspace_id, date),
    CONSTRAINT chk_tokens_non_negative CHECK (tokens_used >= 0 AND tokens_reserved >= 0)
);

CREATE INDEX IF NOT EXISTS idx_workspace_daily_usage_date ON workspace_daily_usage(date);

CREATE TABLE IF NOT EXISTS query_logs (
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

CREATE INDEX IF NOT EXISTS idx_query_logs_workspace ON query_logs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_query_logs_user ON query_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_query_logs_created ON query_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    document_id UUID NULL REFERENCES documents(id) ON DELETE SET NULL,
    document_ids UUID[] NOT NULL DEFAULT '{}',
    title TEXT NOT NULL DEFAULT '',
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS document_ids UUID[] NOT NULL DEFAULT '{}';

UPDATE chat_sessions
SET document_ids = ARRAY[document_id]::UUID[]
WHERE document_id IS NOT NULL
  AND COALESCE(array_length(document_ids, 1), 0) = 0;

CREATE INDEX IF NOT EXISTS idx_chat_sessions_workspace_updated
    ON chat_sessions(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_workspace_document
    ON chat_sessions(workspace_id, document_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_document_ids
    ON chat_sessions USING GIN (document_ids);

COMMIT;
