from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid

from openai import OpenAI
from sqlalchemy import text

from app.config import settings, utc_today
from app.core.errors import BudgetExceededError
from app.core.ingestion_policy import (
    IngestionFailureCategory,
    ingestion_failure_message,
)
from app.core.ingestion_runs import refresh_ingestion_run_status
from app.core.token_budget import commit_usage, release_tokens, reserve_tokens
from app.db.session import SessionLocal

try:
    import tiktoken
except Exception:  # noqa: BLE001
    tiktoken = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
CHUNK_SIZE_TOKENS = 500
OVERLAP_TOKENS = 100


def _failure_message(category: IngestionFailureCategory, detail: str) -> str:
    return ingestion_failure_message(category, detail)[:2000]


def _document_columns(db) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'documents'
            """
        )
    ).scalars()
    return {str(column_name) for column_name in rows}


def _refresh_ingestion_run(
    *, db, workspace_id: uuid.UUID, ingestion_run_id: str | None
) -> None:
    if not ingestion_run_id:
        return
    try:
        run_uuid = uuid.UUID(str(ingestion_run_id))
    except ValueError:
        logger.warning(
            "Skipping ingestion run refresh for invalid run id",
            extra={
                "workspace_id": str(workspace_id),
                "ingestion_run_id": ingestion_run_id,
            },
        )
        return
    refresh_ingestion_run_status(db=db, workspace_id=workspace_id, run_id=run_uuid)


def _set_document_failed(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    error_message: str,
    ingestion_run_id: str | None = None,
) -> None:
    with SessionLocal() as db:
        update_fields = [
            "status = 'failed'",
            "error_message = :error_message",
            "updated_at = NOW()",
        ]
        if "index_finished_at" in _document_columns(db):
            update_fields.append("index_finished_at = NOW()")
        db.execute(
            text(
                f"""
                UPDATE documents
                SET {", ".join(update_fields)}
                WHERE id = :document_id
                  AND workspace_id = :workspace_id
                """
            ),
            {
                "workspace_id": workspace_id,
                "document_id": document_id,
                "error_message": error_message[:2000],
            },
        )
        _refresh_ingestion_run(
            db=db, workspace_id=workspace_id, ingestion_run_id=ingestion_run_id
        )
        db.commit()


def _mark_document_indexed(
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    ingestion_run_id: str | None,
) -> None:
    with SessionLocal() as db:
        update_fields = [
            "status = :final_status",
            "error_message = NULL",
            "updated_at = NOW()",
        ]
        if "index_finished_at" in _document_columns(db):
            update_fields.append("index_finished_at = NOW()")
        db.execute(
            text(
                f"""
                UPDATE documents
                SET {", ".join(update_fields)}
                WHERE id = :document_id
                  AND workspace_id = :workspace_id
                """
            ),
            {
                "workspace_id": workspace_id,
                "document_id": document_id,
                "final_status": "indexed",
            },
        )
        _refresh_ingestion_run(
            db=db, workspace_id=workspace_id, ingestion_run_id=ingestion_run_id
        )
        db.commit()


def _cleanup_index_artifacts(workspace_id: uuid.UUID, document_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        db.execute(
            text(
                """
                DELETE FROM chunk_embeddings
                WHERE workspace_id = :workspace_id
                  AND document_id = :document_id
                """
            ),
            {"workspace_id": workspace_id, "document_id": document_id},
        )
        db.execute(
            text(
                """
                DELETE FROM chunks
                WHERE workspace_id = :workspace_id
                  AND document_id = :document_id
                """
            ),
            {"workspace_id": workspace_id, "document_id": document_id},
        )
        db.commit()


def _estimate_embedding_tokens(text_value: str) -> int:
    return max(1, int(math.ceil((len(text_value) / 4.0) * 1.1)))


def _get_encoding():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001
        return None


def chunk_text(
    text_value: str,
    chunk_size_tokens: int = CHUNK_SIZE_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[str]:
    normalized = (text_value or "").strip()
    if not normalized:
        return []

    encoding = _get_encoding()
    if encoding is None:
        # Fallback approximation aligned to 4 chars/token if tokenizer is unavailable.
        chunk_size_chars = max(1, chunk_size_tokens * 4)
        overlap_chars = max(0, overlap_tokens * 4)
        chunks: list[str] = []
        start = 0
        text_len = len(normalized)
        while start < text_len:
            end = min(text_len, start + chunk_size_chars)
            piece = normalized[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= text_len:
                break
            start = max(0, end - overlap_chars)
        return chunks

    token_ids = encoding.encode(normalized)
    if not token_ids:
        return []

    chunks: list[str] = []
    start = 0
    total = len(token_ids)
    while start < total:
        end = min(total, start + chunk_size_tokens)
        piece = encoding.decode(token_ids[start:end]).strip()
        if piece:
            chunks.append(piece)
        if end >= total:
            break
        start = max(0, end - overlap_tokens)
    return chunks


def _embedding_to_vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.10f}" for value in embedding) + "]"


def _batched(rows: list[dict[str, object]], batch_size: int):
    size = max(1, batch_size)
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def ingest_index(
    workspace_id: str, document_id: str, ingestion_run_id: str | None = None
) -> dict:
    workspace_uuid = uuid.UUID(workspace_id)
    document_uuid = uuid.UUID(document_id)
    usage_date = utc_today()
    outstanding_reservations: list[int] = []
    started_at = time.perf_counter()
    logger.info(
        "ingest_index started",
        extra={
            "workspace_id": workspace_id,
            "document_id": document_id,
            "ingestion_run_id": ingestion_run_id,
        },
    )

    try:
        with SessionLocal() as db:
            document_row = (
                db.execute(
                    text(
                        """
                    SELECT id, status
                    FROM documents
                    WHERE id = :document_id
                      AND workspace_id = :workspace_id
                    LIMIT 1
                    """
                    ),
                    {"workspace_id": workspace_uuid, "document_id": document_uuid},
                )
                .mappings()
                .first()
            )

            if document_row is None:
                raise ValueError("Document not found for workspace")
            accepted_current_statuses = {"extracting", "indexing", "uploaded"}
            if document_row["status"] not in accepted_current_statuses:
                raise ValueError(
                    f"Document status must be extracting, indexing, or uploaded (got: {document_row['status']})"
                )

            update_fields = [
                "status = 'indexing'",
                "error_message = NULL",
                "updated_at = NOW()",
            ]
            columns = _document_columns(db)
            if "index_started_at" in columns:
                update_fields.append("index_started_at = NOW()")
            if "index_finished_at" in columns:
                update_fields.append("index_finished_at = NULL")

            db.execute(
                text(
                    f"""
                    UPDATE documents
                    SET {", ".join(update_fields)}
                    WHERE id = :document_id
                      AND workspace_id = :workspace_id
                    """
                ),
                {"workspace_id": workspace_uuid, "document_id": document_uuid},
            )

            pages = (
                db.execute(
                    text(
                        """
                    SELECT page_number, content
                    FROM document_pages
                    WHERE workspace_id = :workspace_id
                      AND document_id = :document_id
                    ORDER BY page_number ASC
                    """
                    ),
                    {"workspace_id": workspace_uuid, "document_id": document_uuid},
                )
                .mappings()
                .all()
            )

            db.execute(
                text(
                    """
                    DELETE FROM chunk_embeddings
                    WHERE workspace_id = :workspace_id
                      AND document_id = :document_id
                    """
                ),
                {"workspace_id": workspace_uuid, "document_id": document_uuid},
            )
            db.execute(
                text(
                    """
                    DELETE FROM chunks
                    WHERE workspace_id = :workspace_id
                      AND document_id = :document_id
                    """
                ),
                {"workspace_id": workspace_uuid, "document_id": document_uuid},
            )
            db.commit()

        chunk_rows: list[dict[str, object]] = []
        chunk_index = 0
        for page in pages:
            page_number = int(page["page_number"])
            for piece in chunk_text(str(page["content"] or "")):
                chunk_id = uuid.uuid4()
                chunk_rows.append(
                    {
                        "id": chunk_id,
                        "workspace_id": workspace_uuid,
                        "document_id": document_uuid,
                        "page_start": page_number,
                        "page_end": page_number,
                        "chunk_index": chunk_index,
                        "content": piece,
                        "content_hash": hashlib.sha256(
                            piece.encode("utf-8")
                        ).hexdigest(),
                        "token_count": _estimate_embedding_tokens(piece),
                    }
                )
                chunk_index += 1

        if not chunk_rows:
            _set_document_failed(
                workspace_uuid,
                document_uuid,
                _failure_message(
                    IngestionFailureCategory.UNSUPPORTED_CONTENT,
                    "No usable text chunks were created from the extracted PDF text.",
                ),
                ingestion_run_id,
            )
            return {
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "status": "failed",
                "chunks_total": 0,
                "embeddings_total": 0,
                "embedding_tokens_used": 0,
            }

        with SessionLocal() as db:
            db.execute(
                text(
                    """
                    INSERT INTO chunks (
                        id, workspace_id, document_id, page_start, page_end,
                        chunk_index, content, content_hash, token_count
                    )
                    VALUES (
                        :id, :workspace_id, :document_id, :page_start, :page_end,
                        :chunk_index, :content, :content_hash, :token_count
                    )
                    """
                ),
                chunk_rows,
            )
            db.commit()

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.OPENAI_EMBEDDING_TIMEOUT_SECONDS,
        )
        model = settings.EMBEDDING_MODEL
        embedding_batch_size = max(1, int(settings.EMBEDDING_BATCH_SIZE))
        embedding_count = 0
        total_embedding_tokens = 0

        for batch_rows in _batched(chunk_rows, embedding_batch_size):
            batch_started_at = time.perf_counter()
            estimated_tokens = sum(int(row["token_count"]) for row in batch_rows)
            with SessionLocal() as db:
                reserve_tokens(
                    db=db,
                    workspace_id=workspace_uuid,
                    amount=estimated_tokens,
                    usage_date_utc=usage_date,
                    reservation_ttl_seconds=settings.RESERVATION_TTL_SECONDS,
                )
                db.commit()
            outstanding_reservations.append(estimated_tokens)

            response = client.embeddings.create(
                model=model,
                input=[str(row["content"]) for row in batch_rows],
            )
            embedding_api_latency_ms = int(
                (time.perf_counter() - batch_started_at) * 1000
            )
            response_data = list(response.data)
            if len(response_data) != len(batch_rows):
                raise ValueError(
                    f"Embedding batch returned {len(response_data)} vectors for {len(batch_rows)} chunks"
                )
            if all(getattr(item, "index", None) is not None for item in response_data):
                response_data.sort(key=lambda item: int(item.index))

            embedding_rows: list[dict[str, object]] = []
            for row, embedding_data in zip(batch_rows, response_data, strict=True):
                embedding_rows.append(
                    {
                        "chunk_id": row["id"],
                        "workspace_id": workspace_uuid,
                        "document_id": document_uuid,
                        "embedding": _embedding_to_vector_literal(
                            list(embedding_data.embedding)
                        ),
                        "embedding_model": model,
                    }
                )

            with SessionLocal() as db:
                db.execute(
                    text(
                        """
                        INSERT INTO chunk_embeddings (chunk_id, workspace_id, document_id, embedding, embedding_model)
                        VALUES (:chunk_id, :workspace_id, :document_id, CAST(:embedding AS vector), :embedding_model)
                        """
                    ),
                    embedding_rows,
                )
                commit_usage(
                    db=db,
                    workspace_id=workspace_uuid,
                    amount=estimated_tokens,
                    usage_date_utc=usage_date,
                )
                db.commit()
            outstanding_reservations.pop()

            embedding_count += len(batch_rows)
            total_embedding_tokens += estimated_tokens
            logger.info(
                "ingest_index embedding batch completed",
                extra={
                    "workspace_id": workspace_id,
                    "document_id": document_id,
                    "ingestion_run_id": ingestion_run_id,
                    "embedding_batch_size": len(batch_rows),
                    "embedding_tokens": estimated_tokens,
                    "embedding_api_latency_ms": embedding_api_latency_ms,
                },
            )

        final_status = "indexed"
        _mark_document_indexed(
            workspace_id=workspace_uuid,
            document_id=document_uuid,
            ingestion_run_id=ingestion_run_id,
        )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "ingest_index completed",
            extra={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "chunks_total": len(chunk_rows),
                "embeddings_total": embedding_count,
                "embedding_tokens_used": total_embedding_tokens,
                "duration_ms": duration_ms,
            },
        )

        return {
            "document_id": document_id,
            "ingestion_run_id": ingestion_run_id,
            "status": final_status,
            "chunks_total": len(chunk_rows),
            "embeddings_total": embedding_count,
            "embedding_tokens_used": total_embedding_tokens,
        }
    except BudgetExceededError:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        for reserved in reversed(outstanding_reservations):
            try:
                with SessionLocal() as db:
                    release_tokens(
                        db=db,
                        workspace_id=workspace_uuid,
                        amount=reserved,
                        usage_date_utc=usage_date,
                    )
                    db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to release reserved tokens",
                    extra={
                        "document_id": document_id,
                        "ingestion_run_id": ingestion_run_id,
                    },
                )
        _cleanup_index_artifacts(workspace_uuid, document_uuid)
        _set_document_failed(
            workspace_uuid,
            document_uuid,
            _failure_message(
                IngestionFailureCategory.BUDGET,
                "Insufficient token budget for embeddings. Retry after the workspace budget resets.",
            ),
            ingestion_run_id,
        )
        logger.warning(
            "ingest_index budget exceeded",
            extra={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "duration_ms": duration_ms,
            },
        )
        raise
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        for reserved in reversed(outstanding_reservations):
            try:
                with SessionLocal() as db:
                    release_tokens(
                        db=db,
                        workspace_id=workspace_uuid,
                        amount=reserved,
                        usage_date_utc=usage_date,
                    )
                    db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to release reserved tokens",
                    extra={
                        "document_id": document_id,
                        "ingestion_run_id": ingestion_run_id,
                    },
                )

        logger.exception(
            "ingest_index failed",
            extra={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "duration_ms": duration_ms,
            },
        )
        _cleanup_index_artifacts(workspace_uuid, document_uuid)
        _set_document_failed(
            workspace_uuid,
            document_uuid,
            _failure_message(
                IngestionFailureCategory.INDEXING,
                f"Indexing failed while creating chunks or embeddings. Details: {exc}",
            ),
            ingestion_run_id,
        )
        raise
