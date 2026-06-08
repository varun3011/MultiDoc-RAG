from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.ingestion_policy import (
    IngestionFailureCategory,
    ingestion_failure_message,
)
from app.core.ingestion_runs import (
    PROCESSING_DOCUMENT_STATUSES,
    refresh_ingestion_run_status,
)


@dataclass(frozen=True)
class ReconciledDocument:
    id: uuid.UUID
    workspace_id: uuid.UUID
    ingestion_run_id: uuid.UUID | None
    previous_status: str
    status: str
    error_message: str
    updated_at: datetime


@dataclass(frozen=True)
class ReconciledIngestionRun:
    id: uuid.UUID
    workspace_id: uuid.UUID
    status: str


@dataclass(frozen=True)
class IngestionReconciliationResult:
    documents: list[ReconciledDocument]
    runs: list[ReconciledIngestionRun]


def _document_columns(db: Session) -> set[str]:
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


def configured_stale_thresholds(
    *,
    uploaded_seconds: int,
    extracting_seconds: int,
    indexing_seconds: int,
) -> dict[str, int]:
    thresholds = {
        "uploaded": uploaded_seconds,
        "extracting": extracting_seconds,
        "indexing": indexing_seconds,
    }
    return {
        status_name: int(seconds)
        for status_name, seconds in thresholds.items()
        if status_name in PROCESSING_DOCUMENT_STATUSES and int(seconds) > 0
    }


def stale_document_failure_message(*, previous_status: str, threshold_seconds: int) -> str:
    return ingestion_failure_message(
        IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE,
        (
            f"Document stayed in {previous_status} for more than "
            f"{threshold_seconds} seconds. Ingestion may have been interrupted; "
            "retry the document to restart ingestion."
        ),
    )[:2000]


def _active_run_rows(*, db: Session, workspace_id: uuid.UUID | None) -> list[dict[str, uuid.UUID]]:
    workspace_clause = "AND workspace_id = :workspace_id" if workspace_id else ""
    params: dict[str, object] = {}
    if workspace_id:
        params["workspace_id"] = workspace_id
    return list(
        db.execute(
            text(
                f"""
                SELECT id, workspace_id
                FROM ingestion_runs
                WHERE status IN ('preparing', 'processing')
                {workspace_clause}
                """
            ),
            params,
        )
        .mappings()
        .all()
    )


def _stale_document_rows(
    *,
    db: Session,
    workspace_id: uuid.UUID | None,
    previous_status: str,
    cutoff: datetime,
) -> list[dict[str, object]]:
    workspace_clause = "AND workspace_id = :workspace_id" if workspace_id else ""
    params: dict[str, object] = {
        "previous_status": previous_status,
        "cutoff": cutoff,
    }
    if workspace_id:
        params["workspace_id"] = workspace_id
    return list(
        db.execute(
            text(
                f"""
                SELECT id, workspace_id, ingestion_run_id
                FROM documents
                WHERE status = :previous_status
                  AND updated_at < :cutoff
                  {workspace_clause}
                """
            ),
            params,
        )
        .mappings()
        .all()
    )


def reconcile_stale_ingestion(
    *,
    db: Session,
    stale_after_seconds: dict[str, int],
    workspace_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> IngestionReconciliationResult:
    now_utc = now.astimezone(UTC) if now else datetime.now(UTC)
    reconciled_documents: list[ReconciledDocument] = []
    run_keys: set[tuple[uuid.UUID, uuid.UUID]] = set()
    columns = _document_columns(db)

    for previous_status, threshold_seconds in stale_after_seconds.items():
        if previous_status not in PROCESSING_DOCUMENT_STATUSES or threshold_seconds <= 0:
            continue
        cutoff = now_utc - timedelta(seconds=threshold_seconds)
        error_message = stale_document_failure_message(
            previous_status=previous_status,
            threshold_seconds=threshold_seconds,
        )
        rows = _stale_document_rows(
            db=db,
            workspace_id=workspace_id,
            previous_status=previous_status,
            cutoff=cutoff,
        )
        for row in rows:
            document_id = row["id"]
            row_workspace_id = row["workspace_id"]
            ingestion_run_id = row["ingestion_run_id"]
            update_fields = [
                "status = 'failed'",
                "error_message = :error_message",
                "updated_at = :updated_at",
            ]
            if previous_status == "extracting" and "extract_finished_at" in columns:
                update_fields.append("extract_finished_at = :updated_at")
            if previous_status == "indexing" and "index_finished_at" in columns:
                update_fields.append("index_finished_at = :updated_at")
            db.execute(
                text(
                    f"""
                    UPDATE documents
                    SET {", ".join(update_fields)}
                    WHERE id = :document_id
                      AND workspace_id = :workspace_id
                      AND status = :previous_status
                    """
                ),
                {
                    "document_id": document_id,
                    "workspace_id": row_workspace_id,
                    "previous_status": previous_status,
                    "error_message": error_message,
                    "updated_at": now_utc,
                },
            )
            if ingestion_run_id:
                run_keys.add((row_workspace_id, ingestion_run_id))
            reconciled_documents.append(
                ReconciledDocument(
                    id=document_id,
                    workspace_id=row_workspace_id,
                    ingestion_run_id=ingestion_run_id,
                    previous_status=previous_status,
                    status="failed",
                    error_message=error_message,
                    updated_at=now_utc,
                )
            )

    for row in _active_run_rows(db=db, workspace_id=workspace_id):
        run_keys.add((row["workspace_id"], row["id"]))

    reconciled_runs: list[ReconciledIngestionRun] = []
    for run_workspace_id, run_id in sorted(run_keys, key=lambda item: (str(item[0]), str(item[1]))):
        run_status = refresh_ingestion_run_status(
            db=db,
            workspace_id=run_workspace_id,
            run_id=run_id,
            updated_at=now_utc,
        )
        if run_status is not None:
            reconciled_runs.append(
                ReconciledIngestionRun(
                    id=run_id,
                    workspace_id=run_workspace_id,
                    status=run_status,
                )
            )

    return IngestionReconciliationResult(
        documents=reconciled_documents,
        runs=reconciled_runs,
    )
