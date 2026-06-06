from __future__ import annotations

PROCESSING_DOCUMENT_STATUSES = {"uploaded", "extracting", "indexing"}
SUCCESS_DOCUMENT_STATUSES = {"ready", "indexed"}
FAILED_DOCUMENT_STATUSES = {"failed"}
PENDING_DOCUMENT_STATUSES = {"pending_upload", "uploading"}


def empty_document_status_counts() -> dict[str, int]:
    return {
        "pending_upload": 0,
        "uploading": 0,
        "uploaded": 0,
        "extracting": 0,
        "indexing": 0,
        "ready": 0,
        "indexed": 0,
        "failed": 0,
    }


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
