from app.core.ingestion_policy import (
    IngestionFailureCategory,
    failure_category_from_message,
    is_retryable_failure,
)
from app.core.ingestion_reconciliation import (
    configured_stale_thresholds,
    stale_document_failure_message,
)


def test_configured_stale_thresholds_keep_only_active_positive_statuses() -> None:
    assert configured_stale_thresholds(
        uploaded_seconds=3600,
        extracting_seconds=0,
        indexing_seconds=1800,
    ) == {
        "uploaded": 3600,
        "indexing": 1800,
    }


def test_stale_document_failure_message_is_retryable_transient_infrastructure() -> None:
    message = stale_document_failure_message(
        previous_status="indexing",
        threshold_seconds=3600,
    )

    assert (
        failure_category_from_message(message) == IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE
    )
    assert is_retryable_failure(message)
    assert "indexing" in message
    assert "retry" in message
