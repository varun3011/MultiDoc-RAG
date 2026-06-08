from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

DOCUMENT_STATUSES = (
    "pending_upload",
    "uploading",
    "uploaded",
    "extracting",
    "indexing",
    "indexed",
    "ready",
    "failed",
)
PROCESSING_DOCUMENT_STATUSES = {"uploaded", "extracting", "indexing"}
SUCCESS_DOCUMENT_STATUSES = {"ready", "indexed"}
FAILED_DOCUMENT_STATUSES = {"failed"}
PENDING_DOCUMENT_STATUSES = {"pending_upload", "uploading"}


def empty_document_status_counts() -> dict[str, int]:
    return {status_name: 0 for status_name in DOCUMENT_STATUSES}


def derive_ingestion_run_status(
    *,
    status_counts: dict[str, int],
    accepted_documents: int,
    rejected_documents: int,
) -> str:
    if accepted_documents <= 0:
        return "failed" if rejected_documents > 0 else "preparing"

    success_count = sum(
        status_counts.get(status_name, 0) for status_name in SUCCESS_DOCUMENT_STATUSES
    )
    failed_count = sum(
        status_counts.get(status_name, 0) for status_name in FAILED_DOCUMENT_STATUSES
    )
    terminal_count = success_count + failed_count

    if terminal_count >= accepted_documents:
        if failed_count and success_count:
            return "partial"
        if failed_count:
            return "failed"
        return "completed"

    processing_count = sum(
        status_counts.get(status_name, 0) for status_name in PROCESSING_DOCUMENT_STATUSES
    )
    pending_count = sum(
        status_counts.get(status_name, 0) for status_name in PENDING_DOCUMENT_STATUSES
    )

    if processing_count > 0 or pending_count < accepted_documents:
        return "processing"
    return "preparing"


def document_status_counts_for_run(
    *, db: Session, workspace_id: uuid.UUID, run_id: uuid.UUID
) -> dict[str, int]:
    counts = empty_document_status_counts()
    rows = (
        db.execute(
            text(
                """
                SELECT status, COUNT(*) AS count
                FROM documents
                WHERE workspace_id = :workspace_id
                  AND ingestion_run_id = :run_id
                GROUP BY status
                """
            ),
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        .mappings()
        .all()
    )
    for row in rows:
        status_name = str(row["status"])
        counts[status_name] = int(row["count"] or 0)
    counts["total"] = sum(counts.values())
    return counts


def refresh_ingestion_run_status(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    updated_at: datetime | None = None,
) -> str | None:
    run_row = (
        db.execute(
            text(
                """
                SELECT accepted_documents, rejected_documents
                FROM ingestion_runs
                WHERE id = :run_id
                  AND workspace_id = :workspace_id
                LIMIT 1
                """
            ),
            {"workspace_id": workspace_id, "run_id": run_id},
        )
        .mappings()
        .first()
    )
    if run_row is None:
        return None

    status_counts = document_status_counts_for_run(db=db, workspace_id=workspace_id, run_id=run_id)
    run_status = derive_ingestion_run_status(
        status_counts=status_counts,
        accepted_documents=int(run_row["accepted_documents"] or 0),
        rejected_documents=int(run_row["rejected_documents"] or 0),
    )
    db.execute(
        text(
            """
            UPDATE ingestion_runs
            SET status = :status,
                updated_at = :updated_at
            WHERE id = :run_id
              AND workspace_id = :workspace_id
            """
        ),
        {
            "status": run_status,
            "updated_at": updated_at or datetime.now(UTC),
            "workspace_id": workspace_id,
            "run_id": run_id,
        },
    )
    return run_status
