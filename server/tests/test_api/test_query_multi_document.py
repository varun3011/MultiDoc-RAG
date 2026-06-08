from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.api import query
from app.schemas.query import QueryRequest


class FakeResult:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.statements: list[str] = []
        self.params: list[dict[str, object]] = []
        self.commits = 0

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        self.params.append(dict(params or {}))
        return FakeResult(self.rows)

    def commit(self) -> None:
        self.commits += 1

    @property
    def sql(self) -> str:
        return "\n".join(self.statements)


def test_query_request_accepts_legacy_and_multi_document_selection() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()

    legacy = QueryRequest(document_id=first_id, question="What is covered?")
    multi = QueryRequest(document_ids=[first_id, second_id], question="What is covered?")
    combined = QueryRequest(
        document_id=first_id,
        document_ids=[second_id],
        question="What is covered?",
    )

    assert legacy.selected_document_ids == [first_id]
    assert multi.selected_document_ids == [first_id, second_id]
    assert combined.selected_document_ids == [first_id, second_id]


def test_resolve_query_document_ids_allows_indexed_documents(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    db = FakeDb(
        rows=[
            {"id": first_id, "status": "indexed", "page_count": 3},
            {"id": second_id, "status": "ready", "page_count": 4},
        ]
    )
    monkeypatch.setattr(query.settings, "MAX_QUERY_DOCUMENTS", 10)
    monkeypatch.setattr(query.settings, "MAX_QUERY_TOTAL_PAGES", 10)

    resolved = query.resolve_query_document_ids(
        db=db,
        workspace_id=workspace_id,
        payload=QueryRequest(document_ids=[first_id, second_id], question="Question?"),
    )

    assert resolved == [first_id, second_id]
    assert "id IN (__[POSTCOMPILE_document_ids])" in db.sql
    assert db.params[0]["document_ids"] == [first_id, second_id]


def test_resolve_query_document_ids_rejects_oversized_page_selection(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    db = FakeDb(
        rows=[
            {"id": first_id, "status": "indexed", "page_count": 7},
            {"id": second_id, "status": "ready", "page_count": 8},
        ]
    )
    monkeypatch.setattr(query.settings, "MAX_QUERY_DOCUMENTS", 10)
    monkeypatch.setattr(query.settings, "MAX_QUERY_TOTAL_PAGES", 10)

    with pytest.raises(HTTPException) as exc_info:
        query.resolve_query_document_ids(
            db=db,
            workspace_id=workspace_id,
            payload=QueryRequest(document_ids=[first_id, second_id], question="Question?"),
        )

    assert exc_info.value.status_code == 400
    assert "15 pages" in str(exc_info.value.detail)


def test_log_query_records_all_selected_documents(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    document_ids = [uuid.uuid4(), uuid.uuid4()]
    db = FakeDb()
    monkeypatch.setattr(query.settings, "LOG_EACH_QUERY", True)

    query._log_query(
        db=db,
        workspace_id=workspace_id,
        user_id=str(uuid.uuid4()),
        document_ids=document_ids,
        question="Question?",
        retrieved_chunks=[],
        answer_text="Answer",
        error_message=None,
        retrieval_latency_ms=1,
        llm_latency_ms=2,
        total_latency_ms=3,
        embedding_tokens_used=4,
        llm_input_tokens=5,
        llm_output_tokens=6,
        total_tokens_used=10,
    )

    assert db.params[0]["documents_searched"] == document_ids
    assert db.commits == 1
