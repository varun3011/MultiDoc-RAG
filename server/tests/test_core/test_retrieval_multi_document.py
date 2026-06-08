from __future__ import annotations

import uuid

from app.core.retrieval import retrieve_top_k_chunks_for_documents


class FakeResult:
    def mappings(self):
        return self

    def all(self):
        return []


class FakeDb:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, object]] = []

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        self.params.append(dict(params or {}))
        return FakeResult()

    @property
    def sql(self) -> str:
        return "\n".join(self.statements)


def test_retrieve_top_k_chunks_filters_to_selected_documents() -> None:
    workspace_id = uuid.uuid4()
    document_ids = [uuid.uuid4(), uuid.uuid4()]
    db = FakeDb()

    chunks = retrieve_top_k_chunks_for_documents(
        db=db,
        workspace_id=workspace_id,
        document_ids=document_ids,
        query_embedding=[0.1, 0.2, 0.3],
        top_k=5,
    )

    assert chunks == []
    assert "ce.document_id IN (__[POSTCOMPILE_document_ids])" in db.sql
    assert "c.document_id IN (__[POSTCOMPILE_document_ids])" in db.sql
    assert db.params[0]["document_ids"] == document_ids
