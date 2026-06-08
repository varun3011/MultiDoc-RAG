from app.core.ingestion_runs import (
    DOCUMENT_STATUSES,
    PROCESSING_DOCUMENT_STATUSES,
    derive_ingestion_run_status,
    empty_document_status_counts,
)


def test_document_status_vocabulary_matches_runtime_states() -> None:
    assert set(DOCUMENT_STATUSES) == {
        "pending_upload",
        "uploading",
        "uploaded",
        "extracting",
        "indexing",
        "indexed",
        "ready",
        "failed",
    }
    assert PROCESSING_DOCUMENT_STATUSES == {"uploaded", "extracting", "indexing"}


def test_ingestion_run_status_is_processing_while_documents_are_active() -> None:
    counts = empty_document_status_counts()
    counts["uploaded"] = 2
    counts["ready"] = 1

    assert (
        derive_ingestion_run_status(
            status_counts=counts,
            accepted_documents=3,
            rejected_documents=0,
        )
        == "processing"
    )


def test_ingestion_run_status_is_partial_when_success_and_failure_finish() -> None:
    counts = empty_document_status_counts()
    counts["ready"] = 2
    counts["failed"] = 1

    assert (
        derive_ingestion_run_status(
            status_counts=counts,
            accepted_documents=3,
            rejected_documents=0,
        )
        == "partial"
    )


def test_ingestion_run_status_counts_indexed_as_success() -> None:
    counts = empty_document_status_counts()
    counts["indexed"] = 3

    assert (
        derive_ingestion_run_status(
            status_counts=counts,
            accepted_documents=3,
            rejected_documents=0,
        )
        == "completed"
    )


def test_ingestion_run_status_handles_all_rejected_prepare() -> None:
    counts = empty_document_status_counts()

    assert (
        derive_ingestion_run_status(
            status_counts=counts,
            accepted_documents=0,
            rejected_documents=2,
        )
        == "failed"
    )
