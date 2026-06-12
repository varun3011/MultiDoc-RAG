from __future__ import annotations

from types import SimpleNamespace
import uuid

from jobs import ingest_callbacks, ingest_index


class FakeResult:
    def __init__(
        self,
        *,
        first_row: dict[str, object] | None = None,
        all_rows: list[dict[str, object]] | None = None,
        scalar_values: list[object] | None = None,
    ) -> None:
        self.first_row = first_row
        self.all_rows = all_rows or []
        self.scalar_values = scalar_values or []

    def mappings(self):
        return self

    def first(self):
        return self.first_row

    def all(self):
        return self.all_rows

    def scalars(self):
        return iter(self.scalar_values)


class FakeDb:
    def __init__(
        self,
        *,
        document_row: dict[str, object] | None = None,
        status_count_rows: list[dict[str, object]] | None = None,
        columns: set[str] | None = None,
    ) -> None:
        self.document_row = document_row
        self.status_count_rows = status_count_rows or []
        self.columns = columns or set()
        self.statements: list[str] = []
        self.params: list[dict[str, object]] = []
        self.commits = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        self.params.append(dict(params or {}))
        if "FROM information_schema.columns" in sql:
            return FakeResult(scalar_values=list(self.columns))
        if "SELECT status, error_message" in sql:
            return FakeResult(first_row=self.document_row)
        if "r.accepted_documents" in sql and "COUNT(d.id) AS count" in sql:
            return FakeResult(
                all_rows=[
                    {
                        "accepted_documents": 1,
                        "rejected_documents": 0,
                        "status": row["status"],
                        "count": row["count"],
                    }
                    for row in self.status_count_rows
                ]
            )
        return FakeResult()

    def commit(self) -> None:
        self.commits += 1

    @property
    def sql(self) -> str:
        return "\n".join(self.statements)


class FakeSessionLocal:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def __call__(self):
        return self

    def __enter__(self) -> FakeDb:
        return self.db

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False


def test_failure_callback_refreshes_run_for_already_failed_document(
    monkeypatch,
) -> None:
    workspace_id = uuid.uuid4()
    document_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = FakeDb(
        document_row={
            "status": "failed",
            "error_message": "existing structured failure",
        },
        status_count_rows=[{"status": "failed", "count": 1}],
    )
    monkeypatch.setattr(ingest_callbacks, "SessionLocal", FakeSessionLocal(db))
    job = SimpleNamespace(
        id="job-1",
        origin="ingest_extract",
        func_name="jobs.ingest_extract.ingest_extract",
        meta={
            "workspace_id": str(workspace_id),
            "document_id": str(document_id),
            "ingestion_run_id": str(run_id),
        },
    )

    ingest_callbacks.mark_ingestion_job_failed(
        job, None, RuntimeError, RuntimeError("boom"), None
    )

    assert "SET status = 'failed'" not in db.sql
    assert "UPDATE ingestion_runs" in db.sql
    assert any(params.get("status") == "failed" for params in db.params)
    assert db.commits == 1


def test_mark_document_indexed_refreshes_parent_run(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    document_id = uuid.uuid4()
    run_id = uuid.uuid4()
    db = FakeDb(
        status_count_rows=[{"status": "indexed", "count": 1}],
        columns={"index_finished_at"},
    )
    monkeypatch.setattr(ingest_index, "SessionLocal", FakeSessionLocal(db))

    ingest_index._mark_document_indexed(
        workspace_id=workspace_id,
        document_id=document_id,
        ingestion_run_id=str(run_id),
    )

    assert "index_finished_at = NOW()" in db.sql
    assert "UPDATE ingestion_runs" in db.sql
    assert any(params.get("status") == "completed" for params in db.params)
    assert db.commits == 1
