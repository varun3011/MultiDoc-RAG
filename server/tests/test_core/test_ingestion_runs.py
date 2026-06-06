from app.core.ingestion_runs import (
    derive_ingestion_run_status,
    empty_document_status_counts,
)


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
