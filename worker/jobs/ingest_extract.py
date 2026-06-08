from __future__ import annotations

import logging
from pathlib import Path
import time
import uuid

from redis import Redis
from rq import Queue
from rq.job import Callback
from sqlalchemy import text

from app.config import settings
from app.core.ingestion_policy import (
    IngestionFailure,
    IngestionFailureCategory,
    format_bytes,
    ingestion_failure_message,
)
from app.core.ingestion_runs import refresh_ingestion_run_status
from app.core.storage import download_object_bytes
from app.db.session import SessionLocal

try:
    from pypdf import PdfReader
except Exception:  # noqa: BLE001
    from PyPDF2 import PdfReader  # type: ignore[assignment]


logger = logging.getLogger(__name__)
INGESTION_FAILURE_CALLBACK = "jobs.ingest_callbacks.mark_ingestion_job_failed"


def _failure_message(category: IngestionFailureCategory, detail: str) -> str:
    return ingestion_failure_message(category, detail)[:2000]


def _set_document_status(
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    status: str,
    error_message: str | None = None,
    set_now: tuple[str, ...] = (),
    clear: tuple[str, ...] = (),
    ingestion_run_id: str | None = None,
) -> None:
    with SessionLocal() as db:
        columns = _document_columns(db)
        update_fields = [
            "status = :status",
            "error_message = :error_message",
            "updated_at = NOW()",
        ]
        for column_name in set_now:
            if column_name in columns:
                update_fields.append(f"{column_name} = NOW()")
        for column_name in clear:
            if column_name in columns:
                update_fields.append(f"{column_name} = NULL")
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
                "status": status,
                "error_message": error_message,
                "document_id": document_id,
                "workspace_id": workspace_id,
            },
        )
        _refresh_ingestion_run(
            db=db, workspace_id=workspace_id, ingestion_run_id=ingestion_run_id
        )
        db.commit()


def _cleanup_ingestion_artifacts(
    *, workspace_id: uuid.UUID, document_id: uuid.UUID, include_pages: bool
) -> None:
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
        if include_pages:
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
        db.commit()


def _document_columns(db) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'documents'
            """
        ),
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


def _mark_index_enqueued(*, workspace_id: uuid.UUID, document_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        if "index_enqueued_at" not in _document_columns(db):
            return
        db.execute(
            text(
                """
                UPDATE documents
                SET index_enqueued_at = NOW(),
                    updated_at = NOW()
                WHERE id = :document_id
                  AND workspace_id = :workspace_id
                """
            ),
            {"workspace_id": workspace_id, "document_id": document_id},
        )
        db.commit()


def ingest_extract(
    workspace_id: str,
    document_id: str,
    bucket: str,
    storage_path: str,
    ingestion_run_id: str | None = None,
) -> dict:
    workspace_uuid = uuid.UUID(workspace_id)
    document_uuid = uuid.UUID(document_id)
    temp_path = Path(f"/tmp/{document_uuid}.pdf")
    started_at = time.perf_counter()

    _set_document_status(
        workspace_id=workspace_uuid,
        document_id=document_uuid,
        status="extracting",
        set_now=("extract_started_at",),
        clear=(
            "extract_finished_at",
            "index_enqueued_at",
            "index_started_at",
            "index_finished_at",
        ),
    )
    logger.info(
        "ingest_extract started",
        extra={
            "workspace_id": workspace_id,
            "document_id": document_id,
            "ingestion_run_id": ingestion_run_id,
        },
    )

    try:
        try:
            file_bytes = download_object_bytes(bucket=bucket, path=storage_path)
        except Exception as exc:  # noqa: BLE001
            raise IngestionFailure(
                IngestionFailureCategory.UPLOAD_STORAGE,
                "Unable to download the uploaded PDF from storage. Upload the file again and retry.",
            ) from exc

        if len(file_bytes) > settings.MAX_FILE_SIZE_BYTES:
            raise IngestionFailure(
                IngestionFailureCategory.VALIDATION,
                (
                    f"PDF file size must be {format_bytes(settings.MAX_FILE_SIZE_BYTES)} or smaller. "
                    f"Uploaded object size is {format_bytes(len(file_bytes))}."
                ),
            )

        temp_path.write_bytes(file_bytes)

        try:
            reader = PdfReader(str(temp_path))
        except Exception as exc:  # noqa: BLE001
            raise IngestionFailure(
                IngestionFailureCategory.EXTRACTION,
                "PDF could not be read. Upload a valid, unencrypted text-based PDF.",
            ) from exc

        if getattr(reader, "is_encrypted", False):
            raise IngestionFailure(
                IngestionFailureCategory.UNSUPPORTED_CONTENT,
                "Encrypted PDFs are not supported. Upload an unencrypted text-based PDF.",
            )

        pages_total = len(reader.pages)
        if pages_total < 1:
            raise IngestionFailure(
                IngestionFailureCategory.UNSUPPORTED_CONTENT,
                "PDF does not contain any pages.",
            )
        if pages_total > settings.MAX_PDF_PAGE_COUNT:
            raise IngestionFailure(
                IngestionFailureCategory.PAGE_LIMIT,
                f"PDF has {pages_total} pages. Maximum supported page count is {settings.MAX_PDF_PAGE_COUNT}.",
            )

        extracted_pages: list[tuple[int, str]] = []
        total_text_chars = 0
        for page_index, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            extracted_pages.append((page_index + 1, page_text))
            total_text_chars += len(page_text.strip())

        if total_text_chars < settings.MIN_EXTRACTED_TEXT_CHARS:
            raise IngestionFailure(
                IngestionFailureCategory.UNSUPPORTED_CONTENT,
                "No extractable text was found. Scanned/image-only PDFs are not supported.",
            )

        with SessionLocal() as db:
            # Idempotency: reruns overwrite prior extraction for this document.
            db.execute(
                text(
                    """
                    DELETE FROM document_pages
                    WHERE workspace_id = :workspace_id
                      AND document_id = :document_id
                    """
                ),
                {"workspace_id": workspace_uuid, "document_id": document_uuid},
            )

            page_rows = [
                {
                    "workspace_id": workspace_uuid,
                    "document_id": document_uuid,
                    "page_number": page_number,
                    "content": page_text,
                }
                for page_number, page_text in extracted_pages
            ]
            if page_rows:
                db.execute(
                    text(
                        """
                        INSERT INTO document_pages (workspace_id, document_id, page_number, content)
                        VALUES (:workspace_id, :document_id, :page_number, :content)
                        """
                    ),
                    page_rows,
                )

            update_fields = [
                "page_count = :page_count",
                "status = 'indexing'",
                "error_message = NULL",
                "updated_at = NOW()",
            ]
            columns = _document_columns(db)
            if "extract_finished_at" in columns:
                update_fields.append("extract_finished_at = NOW()")
            params: dict[str, object] = {
                "workspace_id": workspace_uuid,
                "document_id": document_uuid,
                "page_count": pages_total,
            }
            if "pages_total" in columns:
                update_fields.append("pages_total = :pages_total")
                params["pages_total"] = pages_total

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
            db.commit()

        redis_conn = Redis.from_url(settings.REDIS_URL)
        queue = Queue("ingest_index", connection=redis_conn)
        queue.enqueue(
            "jobs.ingest_index.ingest_index",
            workspace_id=workspace_id,
            document_id=document_id,
            ingestion_run_id=ingestion_run_id,
            job_timeout=settings.INGEST_INDEX_JOB_TIMEOUT_SECONDS,
            on_failure=Callback(INGESTION_FAILURE_CALLBACK, timeout=60),
            meta={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
            },
        )
        _mark_index_enqueued(workspace_id=workspace_uuid, document_id=document_uuid)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "ingest_extract completed",
            extra={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "pages_total": pages_total,
                "text_chars": total_text_chars,
                "duration_ms": duration_ms,
            },
        )

        return {
            "document_id": document_id,
            "ingestion_run_id": ingestion_run_id,
            "pages_total": pages_total,
            "status": "indexing",
        }
    except IngestionFailure as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning(
            "ingest_extract rejected document",
            extra={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "failure_category": exc.category.value,
                "duration_ms": duration_ms,
            },
        )
        _cleanup_ingestion_artifacts(
            workspace_id=workspace_uuid, document_id=document_uuid, include_pages=True
        )
        _set_document_status(
            workspace_id=workspace_uuid,
            document_id=document_uuid,
            status="failed",
            error_message=str(exc)[:2000],
            set_now=("extract_finished_at",),
            ingestion_run_id=ingestion_run_id,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.exception(
            "ingest_extract failed",
            extra={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "ingestion_run_id": ingestion_run_id,
                "duration_ms": duration_ms,
            },
        )
        _cleanup_ingestion_artifacts(
            workspace_id=workspace_uuid, document_id=document_uuid, include_pages=True
        )
        _set_document_status(
            workspace_id=workspace_uuid,
            document_id=document_uuid,
            status="failed",
            error_message=_failure_message(
                IngestionFailureCategory.EXTRACTION,
                f"Text extraction failed. Upload a valid text-based PDF and retry. Details: {exc}",
            ),
            set_now=("extract_finished_at",),
            ingestion_run_id=ingestion_run_id,
        )
        raise
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
