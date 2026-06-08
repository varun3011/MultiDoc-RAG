from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from app.core.ingestion_policy import (
    IngestionFailureCategory,
    ingestion_failure_message,
)
from app.core.ingestion_runs import refresh_ingestion_run_status
from app.db.session import SessionLocal


logger = logging.getLogger(__name__)


def _failure_message(detail: str) -> str:
    return ingestion_failure_message(
        IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE, detail
    )[:2000]


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


def mark_ingestion_job_failed(job, connection, exc_type, exc_value, traceback) -> None:
    meta = job.meta or {}
    workspace_id = meta.get("workspace_id")
    document_id = meta.get("document_id")
    ingestion_run_id = meta.get("ingestion_run_id")
    if not workspace_id or not document_id:
        logger.warning(
            "Ingestion failure callback skipped job without document metadata",
            extra={"job_id": job.id, "queue": job.origin},
        )
        return

    try:
        workspace_uuid = uuid.UUID(str(workspace_id))
        document_uuid = uuid.UUID(str(document_id))
    except ValueError:
        logger.warning(
            "Ingestion failure callback skipped invalid document metadata",
            extra={
                "job_id": job.id,
                "workspace_id": workspace_id,
                "document_id": document_id,
            },
        )
        return

    detail = f"{job.func_name} failed in queue {job.origin}: {exc_value}"
    error_message = _failure_message(detail)

    with SessionLocal() as db:
        row = (
            db.execute(
                text(
                    """
                    SELECT status, error_message
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
        if row is None:
            return

        # Normal job exceptions already set a structured failure before RQ runs
        # callbacks. Only repair rows left in an active state by timeout/kill,
        # but still refresh the parent run for already-terminal documents.
        should_update_document = not (
            row["status"] == "failed" and row["error_message"]
        )

        if should_update_document:
            update_fields = [
                "status = 'failed'",
                "error_message = :error_message",
                "updated_at = NOW()",
            ]
            columns = _document_columns(db)
            if job.origin == "ingest_extract" and "extract_finished_at" in columns:
                update_fields.append("extract_finished_at = NOW()")
            if job.origin == "ingest_index" and "index_finished_at" in columns:
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
                    "workspace_id": workspace_uuid,
                    "document_id": document_uuid,
                    "error_message": error_message,
                },
            )
        if ingestion_run_id:
            try:
                run_uuid = uuid.UUID(str(ingestion_run_id))
            except ValueError:
                run_uuid = None
            if run_uuid:
                refresh_ingestion_run_status(
                    db=db,
                    workspace_id=workspace_uuid,
                    run_id=run_uuid,
                )
        db.commit()

    logger.warning(
        "Ingestion failure callback marked document failed",
        extra={
            "job_id": job.id,
            "queue": job.origin,
            "workspace_id": workspace_id,
            "document_id": document_id,
            "ingestion_run_id": ingestion_run_id,
        },
    )
