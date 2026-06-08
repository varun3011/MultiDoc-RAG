from dataclasses import dataclass
from typing import Any
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    score: float
    chunk_text: str
    page_text: str
    token_count: int

    @property
    def snippet(self) -> str:
        collapsed = " ".join(self.chunk_text.split())
        return collapsed[:300]


def _embedding_to_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.10f}" for value in embedding) + "]"


def retrieve_top_k_chunks(
    db: Session,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    query_embedding: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    return retrieve_top_k_chunks_for_documents(
        db=db,
        workspace_id=workspace_id,
        document_ids=[document_id],
        query_embedding=query_embedding,
        top_k=top_k,
    )


def retrieve_top_k_chunks_for_documents(
    db: Session,
    workspace_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    query_embedding: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    if not document_ids:
        return []
    vector_literal = _embedding_to_vector_literal(query_embedding)
    sql = text(
        """
        SELECT
            c.id AS chunk_id,
            c.document_id AS document_id,
            c.page_start AS page_number,
            c.content AS chunk_text,
            COALESCE(dp.content, c.content) AS page_text,
            c.token_count AS token_count,
            (ce.embedding <=> CAST(:query_embedding AS vector)) AS score
        FROM chunk_embeddings ce
        JOIN chunks c ON c.id = ce.chunk_id
        LEFT JOIN document_pages dp
            ON dp.workspace_id = :workspace_id
           AND dp.document_id = c.document_id
           AND dp.page_number = c.page_start
        WHERE ce.workspace_id = :workspace_id
          AND ce.document_id IN :document_ids
          AND c.workspace_id = :workspace_id
          AND c.document_id IN :document_ids
        ORDER BY ce.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :top_k
        """
    ).bindparams(bindparam("document_ids", expanding=True))
    rows: list[dict[str, Any]] = (
        db.execute(
            sql,
            {
                "workspace_id": workspace_id,
                "document_ids": document_ids,
                "query_embedding": vector_literal,
                "top_k": top_k,
            },
        )
        .mappings()
        .all()
    )
    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            page_number=int(row["page_number"]),
            score=float(row["score"]),
            chunk_text=row["chunk_text"] or "",
            page_text=row["page_text"] or "",
            token_count=int(row["token_count"] or 0),
        )
        for row in rows
    ]
