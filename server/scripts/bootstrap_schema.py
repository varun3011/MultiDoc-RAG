from __future__ import annotations

from sqlalchemy import text

from app.db import models  # noqa: F401
from app.db.session import Base, engine


DDL_STATEMENTS = [
    """
    CREATE EXTENSION IF NOT EXISTS vector
    """,
    """
    CREATE EXTENSION IF NOT EXISTS pgcrypto
    """,
    """
    CREATE TABLE IF NOT EXISTS document_pages (
        id BIGSERIAL PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL CHECK (page_number > 0),
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (document_id, page_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_pages_workspace
        ON document_pages(workspace_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_pages_document
        ON document_pages(document_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id UUID PRIMARY KEY,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        page_start INTEGER NOT NULL CHECK (page_start > 0),
        page_end INTEGER NOT NULL CHECK (page_end >= page_start),
        chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        token_count INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_document_chunk_index
        ON chunks(document_id, chunk_index)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunks_workspace_document
        ON chunks(workspace_id, document_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS chunk_embeddings (
        chunk_id UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        embedding vector(1536) NOT NULL,
        embedding_model TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_workspace_document
        ON chunk_embeddings(workspace_id, document_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector
        ON chunk_embeddings
        USING hnsw (embedding vector_cosine_ops)
    """,
    """
    CREATE TABLE IF NOT EXISTS query_logs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        query_text TEXT NOT NULL,
        documents_searched UUID[] NOT NULL DEFAULT '{}',
        retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}',
        chunk_scores DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
        answer_text TEXT NULL,
        error_message TEXT NULL,
        retrieval_latency_ms INTEGER NOT NULL DEFAULT 0,
        llm_latency_ms INTEGER NULL,
        total_latency_ms INTEGER NOT NULL DEFAULT 0,
        embedding_tokens_used BIGINT NOT NULL DEFAULT 0,
        llm_input_tokens INTEGER NULL,
        llm_output_tokens INTEGER NULL,
        total_tokens_used BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_query_logs_workspace_created_at
        ON query_logs(workspace_id, created_at DESC)
    """,
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for statement in DDL_STATEMENTS:
            conn.execute(text(statement))
    print("Schema bootstrap completed.")


if __name__ == "__main__":
    main()
