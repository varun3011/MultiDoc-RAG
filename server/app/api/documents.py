from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis import Redis
from rq import Queue
from rq.job import Callback
from rq.registry import (
    DeferredJobRegistry,
    FailedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.config import settings
from app.core.ingestion_runs import (
    derive_ingestion_run_status,
    empty_document_status_counts,
)
from app.core.ingestion_policy import (
    IngestionFailureCategory,
    format_bytes,
    ingestion_failure_message,
    is_retryable_failure,
)
from app.core.storage import delete_object, generate_signed_upload_url, object_exists
from app.db.models import Document
from app.db.session import get_db
from app.schemas.citations import DocumentPageSourceResponse
from app.schemas.documents import (
    DocumentJobResponse,
    DocumentDetailResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentProgress,
    IngestionQueueStatusItem,
    IngestionQueueStatusResponse,
    IngestionRunResponse,
    IngestionRunStatusCounts,
    UploadCompleteBatchItem,
    UploadCompleteBatchRequest,
    UploadCompleteBatchResponse,
    UploadPrepareBatchItem,
    UploadPrepareBatchRequest,
    UploadPrepareBatchResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadPrepareRequest,
    UploadPrepareResponse,
)
from app.utils.rate_limit import (
    BULK_UPLOAD_COMPLETE_RATE_LIMIT,
    BULK_UPLOAD_PREPARE_RATE_LIMIT,
    QUERY_RATE_LIMIT,
    UPLOAD_COMPLETE_RATE_LIMIT,
    UPLOAD_PREPARE_RATE_LIMIT,
    enforce_workspace_rate_limit,
)

router = APIRouter()
logger = logging.getLogger(__name__)
INGESTION_FAILURE_CALLBACK = "jobs.ingest_callbacks.mark_ingestion_job_failed"
ALLOWED_STATUS_FILTERS = {
    "pending_upload",
    "uploading",
    "uploaded",
    "extracting",
    "indexing",
    "indexed",
    "ready",
    "failed",
}


def _idempotency_hash(payload_key: str) -> str:
    # Prefix keeps idempotency markers distinct from real file hashes.
    return f"idemp:{payload_key}"


def _find_existing_prepare_by_idempotency(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    idempotency_hash: str,
) -> dict | None:
    row = (
        db.execute(
            text(
                """
            SELECT id, filename, storage_path, status
            FROM documents
            WHERE workspace_id = :workspace_id
              AND file_hash_sha256 = :idempotency_hash
            LIMIT 1
            """
            ),
            {"workspace_id": workspace_id, "idempotency_hash": idempotency_hash},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "storage_path": row["storage_path"],
        "status": row["status"],
    }


def _sanitize_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_validation_error("Invalid filename.")
        )

    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_validation_error("Invalid filename.")
        )
    return sanitized


def _document_columns(db: Session) -> set[str]:
    bind = db.get_bind()
    return {col["name"] for col in inspect(bind).get_columns("documents")}


def _table_exists(db: Session, table_name: str) -> bool:
    return inspect(db.get_bind()).has_table(table_name)


def _require_ingestion_run_schema(db: Session) -> None:
    if not _table_exists(db, "ingestion_runs") or "ingestion_run_id" not in _document_columns(db):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ingestion-run schema is not initialized. Apply scripts/schema.local.sql or scripts/schema.supabase.sql.",
        )


def _count_for_document(
    db: Session, sql: str, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> int:
    value = db.execute(
        text(sql),
        {
            "workspace_id": workspace_id,
            "document_id": document_id,
        },
    ).scalar_one()
    return int(value or 0)


def _trim_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return f"{value[: max_chars - 3].rstrip()}..."


def _validation_error(detail: str) -> str:
    return ingestion_failure_message(IngestionFailureCategory.VALIDATION, detail)


def _set_document_failed(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    category: IngestionFailureCategory,
    detail: str,
) -> str:
    error_message = ingestion_failure_message(category, detail)
    db.execute(
        text(
            """
            UPDATE documents
            SET status = 'failed',
                error_message = :error_message,
                updated_at = :updated_at
            WHERE id = :document_id
              AND workspace_id = :workspace_id
            """
        ),
        {
            "workspace_id": workspace_id,
            "document_id": document_id,
            "error_message": error_message,
            "updated_at": datetime.now(UTC),
        },
    )
    return error_message


def _validate_upload_file(
    *, filename: str, content_type: str, file_size_bytes: int
) -> tuple[str | None, str | None]:
    if file_size_bytes > settings.MAX_FILE_SIZE_BYTES:
        return (
            None,
            _validation_error(
                f"PDF file size must be {format_bytes(settings.MAX_FILE_SIZE_BYTES)} or smaller."
            ),
        )

    allowed_content_types = {
        allowed_content_type.lower() for allowed_content_type in settings.ALLOWED_CONTENT_TYPES
    }
    if content_type.lower() not in allowed_content_types:
        return (
            None,
            _validation_error(
                f"Only PDF uploads are supported. Allowed content types: {sorted(allowed_content_types)}."
            ),
        )

    try:
        sanitized_filename = _sanitize_filename(filename)
    except HTTPException as exc:
        return None, str(exc.detail)

    if not sanitized_filename.lower().endswith(".pdf"):
        return None, _validation_error("Only files with a .pdf extension are supported.")

    return sanitized_filename, None


def _create_ingestion_run(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    name: str | None,
    total_documents: int,
    accepted_documents: int,
    rejected_documents: int,
) -> None:
    run_status = "processing" if accepted_documents else "failed"
    db.execute(
        text(
            """
            INSERT INTO ingestion_runs (
                id, workspace_id, name, status, total_documents,
                accepted_documents, rejected_documents, created_at, updated_at
            )
            VALUES (
                :id, :workspace_id, :name, :status, :total_documents,
                :accepted_documents, :rejected_documents, :created_at, :updated_at
            )
            """
        ),
        {
            "id": run_id,
            "workspace_id": workspace_id,
            "name": name,
            "status": run_status,
            "total_documents": total_documents,
            "accepted_documents": accepted_documents,
            "rejected_documents": rejected_documents,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    )


def _document_status_counts_for_run(
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


def _refresh_ingestion_run_status(
    *, db: Session, workspace_id: uuid.UUID, run_id: uuid.UUID
) -> str:
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion run not found")

    status_counts = _document_status_counts_for_run(db=db, workspace_id=workspace_id, run_id=run_id)
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
            "updated_at": datetime.now(UTC),
            "workspace_id": workspace_id,
            "run_id": run_id,
        },
    )
    return run_status


def _registry_count(registry) -> int:
    count_value = registry.count
    return int(count_value() if callable(count_value) else count_value)


def _enqueue_extract(
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    bucket: str,
    storage_path: str,
    ingestion_run_id: uuid.UUID | None = None,
):
    redis_conn = Redis.from_url(settings.REDIS_URL)
    queue = Queue("ingest_extract", connection=redis_conn)
    return queue.enqueue(
        "jobs.ingest_extract.ingest_extract",
        workspace_id=str(workspace_id),
        document_id=str(document_id),
        bucket=bucket,
        storage_path=storage_path,
        ingestion_run_id=str(ingestion_run_id) if ingestion_run_id else None,
        job_timeout=settings.INGEST_EXTRACT_JOB_TIMEOUT_SECONDS,
        on_failure=Callback(INGESTION_FAILURE_CALLBACK, timeout=60),
        meta={
            "workspace_id": str(workspace_id),
            "document_id": str(document_id),
            "ingestion_run_id": str(ingestion_run_id) if ingestion_run_id else None,
        },
    )


def _enqueue_index(
    *, workspace_id: uuid.UUID, document_id: uuid.UUID, ingestion_run_id: uuid.UUID | None = None
):
    redis_conn = Redis.from_url(settings.REDIS_URL)
    queue = Queue("ingest_index", connection=redis_conn)
    return queue.enqueue(
        "jobs.ingest_index.ingest_index",
        workspace_id=str(workspace_id),
        document_id=str(document_id),
        ingestion_run_id=str(ingestion_run_id) if ingestion_run_id else None,
        job_timeout=settings.INGEST_INDEX_JOB_TIMEOUT_SECONDS,
        on_failure=Callback(INGESTION_FAILURE_CALLBACK, timeout=60),
        meta={
            "workspace_id": str(workspace_id),
            "document_id": str(document_id),
            "ingestion_run_id": str(ingestion_run_id) if ingestion_run_id else None,
        },
    )


@router.get("", response_model=DocumentListResponse)
def list_documents(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20),
    offset: int = Query(default=0),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 100"
        )
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0")

    if status_filter is not None and status_filter not in ALLOWED_STATUS_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Allowed: {sorted(ALLOWED_STATUS_FILTERS)}",
        )

    columns = _document_columns(db)
    has_content_type = "content_type" in columns
    page_count_select = "page_count" if "page_count" in columns else "NULL AS page_count"
    ingestion_run_id_select = (
        "ingestion_run_id" if "ingestion_run_id" in columns else "NULL AS ingestion_run_id"
    )
    error_message_select = (
        "error_message" if "error_message" in columns else "NULL AS error_message"
    )

    where_clauses = ["workspace_id = :workspace_id"]
    params: dict[str, object] = {"workspace_id": workspace_id, "limit": limit, "offset": offset}
    if status_filter is not None:
        where_clauses.append("status = :status")
        params["status"] = status_filter
    where_sql = " AND ".join(where_clauses)

    total_sql = text(f"SELECT COUNT(*) FROM documents WHERE {where_sql}")
    total = int(db.execute(total_sql, params).scalar_one() or 0)

    content_type_select = (
        "content_type" if has_content_type else "'application/pdf' AS content_type"
    )
    list_sql = text(
        f"""
        SELECT id, filename, {content_type_select}, file_size_bytes, {page_count_select},
               {ingestion_run_id_select}, status, {error_message_select}, created_at, updated_at
        FROM documents
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    rows = db.execute(list_sql, params).mappings().all()

    items = [
        DocumentListItem(
            id=row["id"],
            filename=row["filename"],
            content_type=row["content_type"],
            file_size_bytes=int(row["file_size_bytes"]),
            page_count=row["page_count"],
            ingestion_run_id=row["ingestion_run_id"],
            status=row["status"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]

    return DocumentListResponse(items=items, limit=limit, offset=offset, total=total)


@router.get("/ingestion-queues", response_model=IngestionQueueStatusResponse)
def get_ingestion_queues(
    workspace_id: uuid.UUID = Depends(get_workspace_id),
) -> IngestionQueueStatusResponse:
    redis_conn = Redis.from_url(settings.REDIS_URL)
    queue_items: list[IngestionQueueStatusItem] = []
    for queue_name in ("ingest_extract", "ingest_index"):
        queue = Queue(queue_name, connection=redis_conn)
        queue_items.append(
            IngestionQueueStatusItem(
                name=queue_name,
                queued_count=len(queue),
                started_count=_registry_count(
                    StartedJobRegistry(queue_name, connection=redis_conn)
                ),
                deferred_count=_registry_count(
                    DeferredJobRegistry(queue_name, connection=redis_conn)
                ),
                scheduled_count=_registry_count(
                    ScheduledJobRegistry(queue_name, connection=redis_conn)
                ),
                failed_count=_registry_count(FailedJobRegistry(queue_name, connection=redis_conn)),
            )
        )

    # Keep the endpoint workspace-authenticated even though queue depth is global.
    _ = workspace_id
    return IngestionQueueStatusResponse(queues=queue_items)


@router.get("/ingestion-runs/{run_id}", response_model=IngestionRunResponse)
def get_ingestion_run(
    run_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> IngestionRunResponse:
    _require_ingestion_run_schema(db)
    run_status = _refresh_ingestion_run_status(db=db, workspace_id=workspace_id, run_id=run_id)
    run_row = (
        db.execute(
            text(
                """
                SELECT id, name, total_documents, accepted_documents, rejected_documents,
                       created_at, updated_at
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion run not found")
    status_counts = _document_status_counts_for_run(db=db, workspace_id=workspace_id, run_id=run_id)
    db.commit()

    return IngestionRunResponse(
        id=run_row["id"],
        name=run_row["name"],
        status=run_status,
        total_documents=int(run_row["total_documents"] or 0),
        accepted_documents=int(run_row["accepted_documents"] or 0),
        rejected_documents=int(run_row["rejected_documents"] or 0),
        document_statuses=IngestionRunStatusCounts(**status_counts),
        created_at=run_row["created_at"],
        updated_at=run_row["updated_at"],
    )


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> DocumentDetailResponse:
    columns = _document_columns(db)
    select_fields = [
        "id",
        "filename",
        "file_size_bytes",
        "status",
        "storage_path",
        "created_at",
        "updated_at",
    ]
    if "error_message" in columns:
        select_fields.append("error_message")
    else:
        select_fields.append("NULL AS error_message")
    if "content_type" in columns:
        select_fields.append("content_type")
    else:
        select_fields.append("'application/pdf' AS content_type")
    if "storage_bucket" in columns:
        select_fields.append("storage_bucket")
    else:
        select_fields.append("NULL AS storage_bucket")
    if "ingestion_run_id" in columns:
        select_fields.append("ingestion_run_id")
    else:
        select_fields.append("NULL AS ingestion_run_id")
    if "pages_total" in columns:
        select_fields.append("pages_total")
    else:
        select_fields.append("NULL AS pages_total")

    detail_sql = text(
        f"""
        SELECT {", ".join(select_fields)}
        FROM documents
        WHERE id = :document_id
          AND workspace_id = :workspace_id
        LIMIT 1
        """
    )
    row = (
        db.execute(
            detail_sql,
            {"document_id": document_id, "workspace_id": workspace_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    pages_total = row["pages_total"]
    if pages_total is None:
        pages_total = _count_for_document(
            db,
            "SELECT COUNT(*) FROM document_pages WHERE workspace_id = :workspace_id AND document_id = :document_id",
            workspace_id,
            document_id,
        )
    else:
        pages_total = int(pages_total)

    pages_extracted_count = _count_for_document(
        db,
        """
        SELECT COUNT(*)
        FROM document_pages
        WHERE workspace_id = :workspace_id
          AND document_id = :document_id
          AND NULLIF(BTRIM(content), '') IS NOT NULL
        """,
        workspace_id,
        document_id,
    )
    chunks_count = _count_for_document(
        db,
        "SELECT COUNT(*) FROM chunks WHERE workspace_id = :workspace_id AND document_id = :document_id",
        workspace_id,
        document_id,
    )
    embeddings_count = _count_for_document(
        db,
        "SELECT COUNT(*) FROM chunk_embeddings WHERE workspace_id = :workspace_id AND document_id = :document_id",
        workspace_id,
        document_id,
    )

    return DocumentDetailResponse(
        id=row["id"],
        filename=row["filename"],
        content_type=row["content_type"],
        file_size_bytes=int(row["file_size_bytes"]),
        ingestion_run_id=row["ingestion_run_id"],
        status=row["status"],
        error_message=row["error_message"],
        bucket=row["storage_bucket"] or settings.SUPABASE_STORAGE_BUCKET,
        storage_path=row["storage_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        progress=DocumentProgress(
            pages_total=pages_total,
            pages_extracted_count=pages_extracted_count,
            chunks_count=chunks_count,
            embeddings_count=embeddings_count,
        ),
    )


@router.get("/{document_id}/pages/{page_number}", response_model=DocumentPageSourceResponse)
def get_document_page(
    document_id: uuid.UUID,
    page_number: int,
    max_chars: int = Query(default=5000, ge=1, le=20000),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> DocumentPageSourceResponse:
    if page_number < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="page_number must be >= 1"
        )

    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="query",
        limit=QUERY_RATE_LIMIT,
    )

    row = (
        db.execute(
            text(
                """
            SELECT content
            FROM document_pages
            WHERE workspace_id = :workspace_id
              AND document_id = :document_id
              AND page_number = :page_number
            LIMIT 1
            """
            ),
            {
                "workspace_id": workspace_id,
                "document_id": document_id,
                "page_number": page_number,
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    return DocumentPageSourceResponse(
        document_id=document_id,
        page_number=page_number,
        text=_trim_text(str(row["content"]), max_chars),
    )


@router.post(
    "/upload-prepare", response_model=UploadPrepareResponse, status_code=status.HTTP_201_CREATED
)
def upload_prepare(
    payload: UploadPrepareRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> UploadPrepareResponse:
    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="documents_upload_prepare",
        limit=UPLOAD_PREPARE_RATE_LIMIT,
    )

    sanitized_filename, validation_error = _validate_upload_file(
        filename=payload.filename,
        content_type=payload.content_type,
        file_size_bytes=payload.file_size_bytes,
    )
    if validation_error is not None or sanitized_filename is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=validation_error)

    count_stmt = select(func.count(Document.id)).where(Document.workspace_id == workspace_id)
    document_count = int(db.execute(count_stmt).scalar_one() or 0)
    if document_count >= settings.MAX_DOCUMENTS_PER_WORKSPACE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_validation_error(
                f"Workspace document limit reached. Maximum documents per workspace: {settings.MAX_DOCUMENTS_PER_WORKSPACE}."
            ),
        )

    now = datetime.now(UTC)
    bucket = settings.SUPABASE_STORAGE_BUCKET
    columns = _document_columns(db)
    idempotency_hash: str | None = None

    if payload.idempotency_key and "file_hash_sha256" in columns:
        idempotency_hash = _idempotency_hash(payload.idempotency_key)
        existing = _find_existing_prepare_by_idempotency(
            db=db,
            workspace_id=workspace_id,
            idempotency_hash=idempotency_hash,
        )
        if existing is not None:
            existing_status = str(existing["status"])
            if existing_status not in {"pending_upload", "uploading"}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Upload already prepared for this key (current status: {existing_status})",
                )
            upload_url = generate_signed_upload_url(
                bucket=bucket,
                path=str(existing["storage_path"]),
                expires=settings.UPLOAD_URL_EXPIRES_SECONDS,
            )
            return UploadPrepareResponse(
                document_id=existing["id"],
                bucket=bucket,
                storage_path=str(existing["storage_path"]),
                upload_url=upload_url,
                expires_in=settings.UPLOAD_URL_EXPIRES_SECONDS,
            )

    document_id = uuid.uuid4()
    storage_path = f"{workspace_id}/{document_id}/{sanitized_filename}"
    try:
        upload_url = generate_signed_upload_url(
            bucket=bucket,
            path=storage_path,
            expires=settings.UPLOAD_URL_EXPIRES_SECONDS,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    insert_fields: dict[str, object] = {
        "id": document_id,
        "workspace_id": workspace_id,
        "filename": sanitized_filename,
        "file_size_bytes": payload.file_size_bytes,
        "storage_path": storage_path,
        "status": "pending_upload",
        "created_at": now,
        "updated_at": now,
    }
    if "content_type" in columns:
        insert_fields["content_type"] = payload.content_type
    if "storage_bucket" in columns:
        insert_fields["storage_bucket"] = bucket
    if "file_hash_sha256" in columns:
        insert_fields["file_hash_sha256"] = idempotency_hash or f"uploading:{document_id}"

    keys = list(insert_fields.keys())
    columns_sql = ", ".join(keys)
    values_sql = ", ".join(f":{key}" for key in keys)
    insert_sql = text(f"INSERT INTO documents ({columns_sql}) VALUES ({values_sql})")

    try:
        db.execute(insert_sql, insert_fields)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # If idempotency insert raced with another request, return existing row.
        if idempotency_hash:
            existing = _find_existing_prepare_by_idempotency(
                db=db,
                workspace_id=workspace_id,
                idempotency_hash=idempotency_hash,
            )
            if existing is not None and str(existing["status"]) in {"pending_upload", "uploading"}:
                upload_url = generate_signed_upload_url(
                    bucket=bucket,
                    path=str(existing["storage_path"]),
                    expires=settings.UPLOAD_URL_EXPIRES_SECONDS,
                )
                return UploadPrepareResponse(
                    document_id=existing["id"],
                    bucket=bucket,
                    storage_path=str(existing["storage_path"]),
                    upload_url=upload_url,
                    expires_in=settings.UPLOAD_URL_EXPIRES_SECONDS,
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create upload placeholder: {exc}",
        ) from exc

    return UploadPrepareResponse(
        document_id=document_id,
        bucket=bucket,
        storage_path=storage_path,
        upload_url=upload_url,
        expires_in=settings.UPLOAD_URL_EXPIRES_SECONDS,
    )


@router.post(
    "/upload-prepare-batch",
    response_model=UploadPrepareBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_prepare_batch(
    payload: UploadPrepareBatchRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> UploadPrepareBatchResponse:
    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="documents_upload_prepare_batch",
        limit=BULK_UPLOAD_PREPARE_RATE_LIMIT,
    )
    _require_ingestion_run_schema(db)

    if not payload.files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="files is required")
    if len(payload.files) > settings.MAX_BULK_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch upload supports up to {settings.MAX_BULK_UPLOAD_FILES} files.",
        )

    columns = _document_columns(db)
    bucket = settings.SUPABASE_STORAGE_BUCKET
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    document_count = int(
        db.execute(
            select(func.count(Document.id)).where(Document.workspace_id == workspace_id)
        ).scalar_one()
        or 0
    )
    remaining_new_document_slots = max(0, settings.MAX_DOCUMENTS_PER_WORKSPACE - document_count)

    items: list[UploadPrepareBatchItem] = []
    insert_rows: list[dict[str, object]] = []
    existing_document_ids: list[uuid.UUID] = []
    new_document_count = 0

    for index, file_payload in enumerate(payload.files):
        sanitized_filename, validation_error = _validate_upload_file(
            filename=file_payload.filename,
            content_type=file_payload.content_type,
            file_size_bytes=file_payload.file_size_bytes,
        )
        if validation_error is not None or sanitized_filename is None:
            items.append(
                UploadPrepareBatchItem(
                    index=index,
                    filename=file_payload.filename,
                    client_file_id=file_payload.client_file_id,
                    status="rejected",
                    error=validation_error,
                )
            )
            continue

        idempotency_hash: str | None = None
        if file_payload.idempotency_key and "file_hash_sha256" in columns:
            idempotency_hash = _idempotency_hash(file_payload.idempotency_key)
            existing = _find_existing_prepare_by_idempotency(
                db=db,
                workspace_id=workspace_id,
                idempotency_hash=idempotency_hash,
            )
            if existing is not None:
                existing_status = str(existing["status"])
                if existing_status not in {"pending_upload", "uploading"}:
                    items.append(
                        UploadPrepareBatchItem(
                            index=index,
                            filename=sanitized_filename,
                            client_file_id=file_payload.client_file_id,
                            status="rejected",
                            error=f"Upload already prepared for this key (current status: {existing_status})",
                        )
                    )
                    continue

                upload_url = generate_signed_upload_url(
                    bucket=bucket,
                    path=str(existing["storage_path"]),
                    expires=settings.UPLOAD_URL_EXPIRES_SECONDS,
                )
                existing_document_ids.append(existing["id"])
                items.append(
                    UploadPrepareBatchItem(
                        index=index,
                        filename=sanitized_filename,
                        client_file_id=file_payload.client_file_id,
                        status="prepared",
                        document_id=existing["id"],
                        bucket=bucket,
                        storage_path=str(existing["storage_path"]),
                        upload_url=upload_url,
                        expires_in=settings.UPLOAD_URL_EXPIRES_SECONDS,
                    )
                )
                continue

        if new_document_count >= remaining_new_document_slots:
            items.append(
                UploadPrepareBatchItem(
                    index=index,
                    filename=sanitized_filename,
                    client_file_id=file_payload.client_file_id,
                    status="rejected",
                    error=_validation_error(
                        "Workspace document limit reached. "
                        f"Maximum documents per workspace: {settings.MAX_DOCUMENTS_PER_WORKSPACE}."
                    ),
                )
            )
            continue

        document_id = uuid.uuid4()
        storage_path = f"{workspace_id}/{document_id}/{sanitized_filename}"
        try:
            upload_url = generate_signed_upload_url(
                bucket=bucket,
                path=storage_path,
                expires=settings.UPLOAD_URL_EXPIRES_SECONDS,
            )
        except ValueError as exc:
            items.append(
                UploadPrepareBatchItem(
                    index=index,
                    filename=sanitized_filename,
                    client_file_id=file_payload.client_file_id,
                    status="rejected",
                    error=str(exc),
                )
            )
            continue

        insert_fields: dict[str, object] = {
            "id": document_id,
            "workspace_id": workspace_id,
            "filename": sanitized_filename,
            "file_size_bytes": file_payload.file_size_bytes,
            "storage_path": storage_path,
            "status": "pending_upload",
            "ingestion_run_id": run_id,
            "created_at": now,
            "updated_at": now,
        }
        if "content_type" in columns:
            insert_fields["content_type"] = file_payload.content_type
        if "storage_bucket" in columns:
            insert_fields["storage_bucket"] = bucket
        if "file_hash_sha256" in columns:
            insert_fields["file_hash_sha256"] = idempotency_hash or f"uploading:{document_id}"
        insert_rows.append(insert_fields)
        new_document_count += 1

        items.append(
            UploadPrepareBatchItem(
                index=index,
                filename=sanitized_filename,
                client_file_id=file_payload.client_file_id,
                status="prepared",
                document_id=document_id,
                bucket=bucket,
                storage_path=storage_path,
                upload_url=upload_url,
                expires_in=settings.UPLOAD_URL_EXPIRES_SECONDS,
            )
        )

    accepted_count = sum(1 for item in items if item.status == "prepared")
    rejected_count = len(items) - accepted_count

    try:
        _create_ingestion_run(
            db=db,
            workspace_id=workspace_id,
            run_id=run_id,
            name=payload.name,
            total_documents=len(payload.files),
            accepted_documents=accepted_count,
            rejected_documents=rejected_count,
        )
        if insert_rows:
            keys = list(insert_rows[0].keys())
            columns_sql = ", ".join(keys)
            values_sql = ", ".join(f":{key}" for key in keys)
            db.execute(
                text(f"INSERT INTO documents ({columns_sql}) VALUES ({values_sql})"),
                insert_rows,
            )
        if existing_document_ids:
            for existing_document_id in existing_document_ids:
                db.execute(
                    text(
                        """
                        UPDATE documents
                        SET ingestion_run_id = :run_id,
                            updated_at = :updated_at
                        WHERE workspace_id = :workspace_id
                          AND id = :document_id
                        """
                    ),
                    {
                        "workspace_id": workspace_id,
                        "document_id": existing_document_id,
                        "run_id": run_id,
                        "updated_at": datetime.now(UTC),
                    },
                )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create batch upload placeholders: {exc}",
        ) from exc

    return UploadPrepareBatchResponse(
        ingestion_run_id=run_id,
        bucket=bucket,
        expires_in=settings.UPLOAD_URL_EXPIRES_SECONDS,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        items=items,
    )


def _complete_upload_document(
    *,
    payload: UploadCompleteRequest,
    workspace_id: uuid.UUID,
    db: Session,
    expected_run_id: uuid.UUID | None = None,
) -> UploadCompleteResponse:
    columns = _document_columns(db)
    select_fields = ["id", "status", "storage_path"]
    if "storage_bucket" in columns:
        select_fields.append("storage_bucket")
    else:
        select_fields.append("NULL AS storage_bucket")
    if "error_message" in columns:
        select_fields.append("error_message")
    else:
        select_fields.append("NULL AS error_message")
    if "ingestion_run_id" in columns:
        select_fields.append("ingestion_run_id")
    else:
        select_fields.append("NULL AS ingestion_run_id")

    row = (
        db.execute(
            text(
                f"""
                SELECT {", ".join(select_fields)}
                FROM documents
                WHERE id = :document_id
                  AND workspace_id = :workspace_id
                LIMIT 1
                """
            ),
            {"document_id": payload.document_id, "workspace_id": workspace_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    row_run_id = row["ingestion_run_id"]
    if expected_run_id is not None and str(row_run_id) != str(expected_run_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document does not belong to the requested ingestion run",
        )

    stored_bucket = row["storage_bucket"] or settings.SUPABASE_STORAGE_BUCKET
    if payload.bucket != stored_bucket or payload.storage_path != row["storage_path"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bucket/path mismatch for document",
        )

    if row["status"] not in {"uploading", "pending_upload"}:
        if row["status"] == "uploaded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Upload already completed",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document is not in uploading state (current: {row['status']})",
        )

    exists = object_exists(bucket=payload.bucket, path=payload.storage_path)
    if exists is False:
        error_message = _set_document_failed(
            db=db,
            workspace_id=workspace_id,
            document_id=payload.document_id,
            category=IngestionFailureCategory.UPLOAD_STORAGE,
            detail="Uploaded object was not found in storage. Upload the PDF again before starting ingestion.",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message,
        )

    update_fields = ["status = 'uploaded'", "updated_at = :updated_at"]
    if "error_message" in columns:
        update_fields.append("error_message = NULL")
    update_result = db.execute(
        text(
            f"""
            UPDATE documents
            SET {", ".join(update_fields)}
            WHERE id = :document_id
              AND workspace_id = :workspace_id
              AND status IN ('uploading', 'pending_upload')
            """
        ),
        {
            "updated_at": datetime.now(UTC),
            "document_id": payload.document_id,
            "workspace_id": workspace_id,
        },
    )
    if update_result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload completion conflict",
        )
    db.commit()

    try:
        job = _enqueue_extract(
            workspace_id=workspace_id,
            document_id=payload.document_id,
            bucket=payload.bucket,
            storage_path=payload.storage_path,
            ingestion_run_id=row_run_id,
        )
    except Exception as exc:  # noqa: BLE001
        _set_document_failed(
            db=db,
            workspace_id=workspace_id,
            document_id=payload.document_id,
            category=IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE,
            detail=f"Failed to enqueue ingestion job: {exc}",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enqueue ingestion job: {exc}",
        ) from exc

    return UploadCompleteResponse(
        document_id=payload.document_id,
        status="uploaded",
        job_id=job.id,
    )


@router.post("/upload-complete", response_model=UploadCompleteResponse)
def upload_complete(
    payload: UploadCompleteRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> UploadCompleteResponse:
    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="documents_upload_complete",
        limit=UPLOAD_COMPLETE_RATE_LIMIT,
    )

    return _complete_upload_document(payload=payload, workspace_id=workspace_id, db=db)


@router.post("/upload-complete-batch", response_model=UploadCompleteBatchResponse)
def upload_complete_batch(
    payload: UploadCompleteBatchRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> UploadCompleteBatchResponse:
    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="documents_upload_complete_batch",
        limit=BULK_UPLOAD_COMPLETE_RATE_LIMIT,
    )
    if not payload.files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="files is required")
    if len(payload.files) > settings.MAX_BULK_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch completion supports up to {settings.MAX_BULK_UPLOAD_FILES} files.",
        )
    if payload.ingestion_run_id is not None:
        _require_ingestion_run_schema(db)

    items: list[UploadCompleteBatchItem] = []
    accepted_count = 0
    failed_count = 0
    for index, file_payload in enumerate(payload.files):
        try:
            completed = _complete_upload_document(
                payload=file_payload,
                workspace_id=workspace_id,
                db=db,
                expected_run_id=payload.ingestion_run_id,
            )
            accepted_count += 1
            items.append(
                UploadCompleteBatchItem(
                    index=index,
                    document_id=completed.document_id,
                    status=completed.status,
                    job_id=completed.job_id,
                )
            )
        except HTTPException as exc:
            failed_count += 1
            items.append(
                UploadCompleteBatchItem(
                    index=index,
                    document_id=file_payload.document_id,
                    status="failed",
                    error=str(exc.detail),
                )
            )

    if payload.ingestion_run_id is not None:
        _refresh_ingestion_run_status(
            db=db, workspace_id=workspace_id, run_id=payload.ingestion_run_id
        )
        db.commit()

    return UploadCompleteBatchResponse(
        ingestion_run_id=payload.ingestion_run_id,
        accepted_count=accepted_count,
        failed_count=failed_count,
        items=items,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> Response:
    columns = _document_columns(db)
    storage_bucket_select = (
        "storage_bucket" if "storage_bucket" in columns else "NULL::text AS storage_bucket"
    )
    delete_sql = text(
        f"""
        DELETE FROM documents
        WHERE id = :document_id
          AND workspace_id = :workspace_id
        RETURNING storage_path, {storage_bucket_select}
        """
    )
    try:
        doc_row = (
            db.execute(
                delete_sql,
                {"workspace_id": workspace_id, "document_id": document_id},
            )
            .mappings()
            .first()
        )
        if doc_row is None:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Document delete failed",
            extra={"workspace_id": str(workspace_id), "document_id": str(document_id)},
        )
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {exc}",
        ) from exc

    bucket = doc_row["storage_bucket"] or settings.SUPABASE_STORAGE_BUCKET
    storage_path = doc_row["storage_path"]
    try:
        delete_object(bucket=bucket, path=storage_path)
    except Exception:  # noqa: BLE001
        # Keep API success after DB delete; storage cleanup can be retried asynchronously.
        logger.exception(
            "Storage delete failed after document metadata delete",
            extra={
                "workspace_id": str(workspace_id),
                "document_id": str(document_id),
                "bucket": str(bucket),
                "storage_path": str(storage_path),
            },
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/retry", response_model=DocumentJobResponse)
def retry_document(
    document_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> DocumentJobResponse:
    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="documents_upload_complete",
        limit=UPLOAD_COMPLETE_RATE_LIMIT,
    )

    columns = _document_columns(db)
    select_fields = ["id", "status", "storage_path"]
    if "storage_bucket" in columns:
        select_fields.append("storage_bucket")
    else:
        select_fields.append("NULL AS storage_bucket")
    if "error_message" in columns:
        select_fields.append("error_message")
    else:
        select_fields.append("NULL AS error_message")
    if "ingestion_run_id" in columns:
        select_fields.append("ingestion_run_id")
    else:
        select_fields.append("NULL AS ingestion_run_id")

    row = (
        db.execute(
            text(
                f"""
            SELECT {", ".join(select_fields)}
            FROM documents
            WHERE id = :document_id
              AND workspace_id = :workspace_id
            LIMIT 1
            """
            ),
            {"document_id": document_id, "workspace_id": workspace_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if row["status"] != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Retry is only allowed for failed documents",
        )
    if not is_retryable_failure(row["error_message"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Retry is not allowed for this failure. {row['error_message']}",
        )

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
    db.execute(
        text(
            """
            DELETE FROM document_pages
            WHERE workspace_id = :workspace_id
              AND document_id = :document_id
            """
        ),
        {"workspace_id": workspace_id, "document_id": document_id},
    )

    update_fields = ["status = 'uploaded'", "updated_at = :updated_at"]
    params: dict[str, object] = {
        "updated_at": datetime.now(UTC),
        "document_id": document_id,
        "workspace_id": workspace_id,
    }
    if "error_message" in columns:
        update_fields.append("error_message = NULL")
    if "page_count" in columns:
        update_fields.append("page_count = NULL")

    db.execute(
        text(
            f"""
            UPDATE documents
            SET {", ".join(update_fields)}
            WHERE id = :document_id
              AND workspace_id = :workspace_id
            """
        ),
        params,
    )

    try:
        job = _enqueue_extract(
            workspace_id=workspace_id,
            document_id=document_id,
            bucket=row["storage_bucket"] or settings.SUPABASE_STORAGE_BUCKET,
            storage_path=row["storage_path"],
            ingestion_run_id=row["ingestion_run_id"],
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enqueue retry job: {exc}",
        ) from exc

    db.commit()
    return DocumentJobResponse(document_id=document_id, status="uploaded", job_id=job.id)


@router.post("/{document_id}/reindex", response_model=DocumentJobResponse)
def reindex_document(
    document_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> DocumentJobResponse:
    enforce_workspace_rate_limit(
        workspace_id=workspace_id,
        operation="documents_upload_complete",
        limit=UPLOAD_COMPLETE_RATE_LIMIT,
    )

    columns = _document_columns(db)
    select_fields = ["id", "status", "storage_path"]
    if "storage_bucket" in columns:
        select_fields.append("storage_bucket")
    else:
        select_fields.append("NULL AS storage_bucket")
    if "error_message" in columns:
        select_fields.append("error_message")
    else:
        select_fields.append("NULL AS error_message")
    if "ingestion_run_id" in columns:
        select_fields.append("ingestion_run_id")
    else:
        select_fields.append("NULL AS ingestion_run_id")

    row = (
        db.execute(
            text(
                f"""
            SELECT {", ".join(select_fields)}
            FROM documents
            WHERE id = :document_id
              AND workspace_id = :workspace_id
            LIMIT 1
            """
            ),
            {"document_id": document_id, "workspace_id": workspace_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if row["status"] not in {"ready", "indexed", "failed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reindex is only allowed for ready/indexed/failed documents",
        )
    if row["status"] == "failed" and not is_retryable_failure(row["error_message"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Reindex is not allowed for this failure. {row['error_message']}",
        )

    pages_count = _count_for_document(
        db,
        "SELECT COUNT(*) FROM document_pages WHERE workspace_id = :workspace_id AND document_id = :document_id",
        workspace_id,
        document_id,
    )
    has_pages = pages_count > 0
    next_status = "indexing" if has_pages else "uploaded"

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

    update_fields = ["status = :status", "updated_at = :updated_at"]
    params = {
        "status": next_status,
        "updated_at": datetime.now(UTC),
        "document_id": document_id,
        "workspace_id": workspace_id,
    }
    if "error_message" in columns:
        update_fields.append("error_message = NULL")

    db.execute(
        text(
            f"""
            UPDATE documents
            SET {", ".join(update_fields)}
            WHERE id = :document_id
              AND workspace_id = :workspace_id
            """
        ),
        params,
    )

    try:
        if has_pages:
            job = _enqueue_index(
                workspace_id=workspace_id,
                document_id=document_id,
                ingestion_run_id=row["ingestion_run_id"],
            )
        else:
            job = _enqueue_extract(
                workspace_id=workspace_id,
                document_id=document_id,
                bucket=row["storage_bucket"] or settings.SUPABASE_STORAGE_BUCKET,
                storage_path=row["storage_path"],
                ingestion_run_id=row["ingestion_run_id"],
            )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        try:
            db.execute(
                text(
                    """
                    UPDATE documents
                    SET status = 'failed',
                        error_message = :error_message,
                        updated_at = :updated_at
                    WHERE id = :document_id
                      AND workspace_id = :workspace_id
                    """
                ),
                {
                    "error_message": str(exc)[:2000],
                    "updated_at": datetime.now(UTC),
                    "document_id": document_id,
                    "workspace_id": workspace_id,
                },
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enqueue reindex job: {exc}",
        ) from exc

    db.commit()
    return DocumentJobResponse(document_id=document_id, status=next_status, job_id=job.id)
