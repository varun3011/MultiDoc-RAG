from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from app.core.ingestion_policy import (
    IngestionFailureCategory,
    ingestion_failure_message,
)
from app.db.session import SessionLocal


logger = logging.getLogger(__name__)


def _failure_message(detail: str) -> str:
    return ingestion_failure_message(
        IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE, detail
    )[:2000]


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
        # callbacks. Only repair rows left in an active state by timeout/kill.
        if row["status"] == "failed" and row["error_message"]:
            return

        db.execute(
            text(
                """
                UPDATE documents
                SET status = 'failed',
                    error_message = :error_message,
                    updated_at = NOW()
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
