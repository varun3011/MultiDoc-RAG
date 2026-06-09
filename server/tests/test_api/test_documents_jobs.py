from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.api import documents
from app.core.ingestion_policy import (
    IngestionFailureCategory,
    ingestion_failure_message,
)


TIMING_COLUMNS = {
    "error_message",
    "storage_bucket",
    "ingestion_run_id",
    "page_count",
    "upload_completed_at",
    "extract_enqueued_at",
    "extract_started_at",
    "extract_finished_at",
    "index_enqueued_at",
    "index_started_at",
    "index_finished_at",
}


class FakeResult:
    def __init__(self, *, first_row=None, scalar_value=0) -> None:
        self.first_row = first_row
        self.scalar_value = scalar_value

    def mappings(self):
        return self

    def first(self):
        return self.first_row

    def scalar_one(self):
        return self.scalar_value


class FakeDb:
    def __init__(self, *, document_row: dict[str, object], pages_count: int = 0) -> None:
        self.document_row = document_row
        self.pages_count = pages_count
        self.statements: list[str] = []
        self.params: list[dict[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        self.params.append(dict(params or {}))
        if "FROM documents" in sql and "LIMIT 1" in sql:
            return FakeResult(first_row=self.document_row)
        if "COUNT(*) FROM document_pages" in sql:
            return FakeResult(scalar_value=self.pages_count)
        return FakeResult()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    @property
    def sql(self) -> str:
        return "\n".join(self.statements)


@pytest.fixture(autouse=True)
def bypass_external_dependencies(monkeypatch):
    monkeypatch.setattr(documents, "enforce_workspace_rate_limit", lambda **kwargs: None)
    monkeypatch.setattr(documents, "_document_columns", lambda db: TIMING_COLUMNS)


def _retryable_error() -> str:
    return ingestion_failure_message(
        IngestionFailureCategory.TRANSIENT_INFRASTRUCTURE,
        "Worker exited before completing ingestion.",
    )


def _terminal_error() -> str:
    return ingestion_failure_message(
        IngestionFailureCategory.PAGE_LIMIT,
        "PDF has 28 pages. Maximum supported page count is 10.",
    )


def _document_row(*, status: str, error_message: str | None = None) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "status": status,
        "storage_path": "workspace/document/file.pdf",
        "storage_bucket": "documents",
        "error_message": error_message,
        "ingestion_run_id": uuid.uuid4(),
    }


def test_retry_rejects_terminal_document_failures(monkeypatch) -> None:
    db = FakeDb(document_row=_document_row(status="failed", error_message=_terminal_error()))
    monkeypatch.setattr(
        documents,
        "_enqueue_extract",
        lambda **kwargs: pytest.fail("terminal failures must not enqueue retry jobs"),
    )

    with pytest.raises(HTTPException) as exc_info:
        documents.retry_document(uuid.uuid4(), workspace_id=uuid.uuid4(), db=db)

    assert exc_info.value.status_code == 409
    assert "Retry is not allowed" in str(exc_info.value.detail)
    assert db.commits == 0


def test_retry_clears_artifacts_resets_timing_and_enqueues_extract(monkeypatch) -> None:
    document_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    row = _document_row(status="failed", error_message=_retryable_error())
    db = FakeDb(document_row=row)
    enqueued: dict[str, object] = {}

    def fake_enqueue_extract(**kwargs):
        enqueued.update(kwargs)
        return SimpleNamespace(id="extract-job")

    monkeypatch.setattr(documents, "_enqueue_extract", fake_enqueue_extract)

    response = documents.retry_document(document_id, workspace_id=workspace_id, db=db)

    assert response.status == "uploaded"
    assert response.job_id == "extract-job"
    assert "DELETE FROM chunk_embeddings" in db.sql
    assert "DELETE FROM chunks" in db.sql
    assert "DELETE FROM document_pages" in db.sql
    assert "extract_enqueued_at = :updated_at" in db.sql
    assert "extract_started_at = NULL" in db.sql
    assert "index_finished_at = NULL" in db.sql
    assert enqueued["document_id"] == document_id
    assert enqueued["workspace_id"] == workspace_id
    assert db.commits == 1


def test_reindex_with_existing_pages_enqueues_index(monkeypatch) -> None:
    document_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    db = FakeDb(document_row=_document_row(status="indexed"), pages_count=2)
    enqueued: dict[str, object] = {}

    def fake_enqueue_index(**kwargs):
        enqueued.update(kwargs)
        return SimpleNamespace(id="index-job")

    monkeypatch.setattr(documents, "_enqueue_index", fake_enqueue_index)
    monkeypatch.setattr(
        documents,
        "_enqueue_extract",
        lambda **kwargs: pytest.fail("documents with existing pages should enqueue index"),
    )

    response = documents.reindex_document(document_id, workspace_id=workspace_id, db=db)

    assert response.status == "indexing"
    assert response.job_id == "index-job"
    assert "index_enqueued_at = :updated_at" in db.sql
    assert "index_started_at = NULL" in db.sql
    assert enqueued["document_id"] == document_id
    assert enqueued["workspace_id"] == workspace_id


def test_reindex_without_pages_enqueues_extract(monkeypatch) -> None:
    document_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    db = FakeDb(document_row=_document_row(status="ready"), pages_count=0)
    enqueued: dict[str, object] = {}

    def fake_enqueue_extract(**kwargs):
        enqueued.update(kwargs)
        return SimpleNamespace(id="extract-job")

    monkeypatch.setattr(documents, "_enqueue_extract", fake_enqueue_extract)
    monkeypatch.setattr(
        documents,
        "_enqueue_index",
        lambda **kwargs: pytest.fail("documents without pages should enqueue extract"),
    )

    response = documents.reindex_document(document_id, workspace_id=workspace_id, db=db)

    assert response.status == "uploaded"
    assert response.job_id == "extract-job"
    assert "extract_enqueued_at = :updated_at" in db.sql
    assert "extract_started_at = NULL" in db.sql
    assert "index_enqueued_at = NULL" in db.sql
    assert enqueued["document_id"] == document_id
    assert enqueued["workspace_id"] == workspace_id


def test_failure_summary_separates_expected_rejections_from_infrastructure_failures() -> None:
    summary = documents._failure_summary(
        [
            ingestion_failure_message(
                IngestionFailureCategory.PAGE_LIMIT,
                "PDF has too many pages.",
            ),
            ingestion_failure_message(
                IngestionFailureCategory.INDEXING,
                "OpenAI request failed.",
            ),
            "legacy raw exception",
        ]
    )

    assert summary.total_failed == 3
    assert summary.expected_rejections == 1
    assert summary.infrastructure_failures == 1
    assert summary.unknown_failures == 1
