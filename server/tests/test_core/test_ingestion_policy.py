from app.core.ingestion_policy import (
    IngestionFailureCategory,
    failure_category_from_message,
    ingestion_failure_message,
    is_retryable_failure,
)


def test_ingestion_failure_messages_are_categorized() -> None:
    message = ingestion_failure_message(
        IngestionFailureCategory.PAGE_LIMIT,
        "PDF has 12 pages. Maximum supported page count is 10.",
    )

    assert message.startswith("Page-limit failure:")
    assert failure_category_from_message(message) == IngestionFailureCategory.PAGE_LIMIT


def test_retryability_uses_failure_category() -> None:
    terminal_message = ingestion_failure_message(
        IngestionFailureCategory.UNSUPPORTED_CONTENT,
        "No extractable text was found.",
    )
    retryable_message = ingestion_failure_message(
        IngestionFailureCategory.INDEXING,
        "Embedding API failed.",
    )

    assert not is_retryable_failure(terminal_message)
    assert is_retryable_failure(retryable_message)
    assert is_retryable_failure("legacy raw exception")
