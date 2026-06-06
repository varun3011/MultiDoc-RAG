from __future__ import annotations

from enum import Enum


class IngestionFailureCategory(str, Enum):
    VALIDATION = "validation"
    UPLOAD_STORAGE = "upload_storage"
    EXTRACTION = "extraction"
    PAGE_LIMIT = "page_limit"
    UNSUPPORTED_CONTENT = "unsupported_content"
    INDEXING = "indexing"
    BUDGET = "budget"
    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"


FAILURE_LABELS: dict[IngestionFailureCategory, str] = {
    IngestionFailureCategory.VALIDATION: "Validation failure",
    IngestionFailureCategory.UPLOAD_STORAGE: "Upload/storage failure",
    IngestionFailureCategory.EXTRACTION: "Extraction failure",
    IngestionFailureCategory.PAGE_LIMIT: "Page-limit failure",
    IngestionFailureCategory.UNSUPPORTED_CONTENT: "Unsupported-content failure",
    IngestionFailureCategory.INDEXING: "Indexing failure",
    IngestionFailureCategory.BUDGET: "Budget failure",
    IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE: "Transient infrastructure failure",
}

RETRYABLE_FAILURE_CATEGORIES = {
    IngestionFailureCategory.EXTRACTION,
    IngestionFailureCategory.INDEXING,
    IngestionFailureCategory.BUDGET,
    IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE,
}

TERMINAL_FAILURE_CATEGORIES = {
    IngestionFailureCategory.VALIDATION,
    IngestionFailureCategory.UPLOAD_STORAGE,
    IngestionFailureCategory.PAGE_LIMIT,
    IngestionFailureCategory.UNSUPPORTED_CONTENT,
}


def format_bytes(size_bytes: int) -> str:
    mib = 1024 * 1024
    if size_bytes % mib == 0:
        return f"{size_bytes // mib} MB"
    return f"{size_bytes} bytes"


def ingestion_failure_message(category: IngestionFailureCategory, detail: str) -> str:
    cleaned_detail = " ".join(str(detail).split())
    return f"{FAILURE_LABELS[category]}: {cleaned_detail}"


class IngestionFailure(Exception):
    def __init__(self, category: IngestionFailureCategory, detail: str) -> None:
        self.category = category
        self.detail = detail
        super().__init__(ingestion_failure_message(category, detail))


def failure_category_from_message(error_message: str | None) -> IngestionFailureCategory | None:
    if not error_message:
        return None
    for category, label in FAILURE_LABELS.items():
        if error_message.startswith(f"{label}:"):
            return category
    return None


def is_retryable_failure(error_message: str | None) -> bool:
    category = failure_category_from_message(error_message)
    if category is None:
        # Legacy failures did not have structured categories; keep them retryable.
        return True
    return category in RETRYABLE_FAILURE_CATEGORIES
