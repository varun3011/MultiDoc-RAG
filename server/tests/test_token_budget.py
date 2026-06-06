from __future__ import annotations

from datetime import UTC, date, datetime
import os
import threading
import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.core.errors import BudgetExceededError
from app.core.token_budget import commit_usage, get_budget_status, release_tokens, reserve_tokens
from app.db.models import Workspace


@pytest.fixture
def limit_guard():
    original_limit = settings.DAILY_TOKEN_LIMIT
    try:
        yield
    finally:
        settings.DAILY_TOKEN_LIMIT = original_limit


def test_reserve_within_budget(db_session: Session, workspace_id: uuid.UUID, limit_guard) -> None:
    settings.DAILY_TOKEN_LIMIT = 1_000
    today = date(2026, 2, 15)

    result = reserve_tokens(db_session, workspace_id, 250, today)

    assert result["reserved"] == 250
    assert result["remaining"] == 750
    assert result["limit"] == 1_000


def test_reserve_exceeding_budget_raises(db_session: Session, workspace_id: uuid.UUID, limit_guard) -> None:
    settings.DAILY_TOKEN_LIMIT = 100
    today = date(2026, 2, 15)
    reserve_tokens(db_session, workspace_id, 80, today)

    with pytest.raises(BudgetExceededError):
        reserve_tokens(db_session, workspace_id, 25, today)


def test_commit_moves_reserved_to_used(db_session: Session, workspace_id: uuid.UUID, limit_guard) -> None:
    settings.DAILY_TOKEN_LIMIT = 1_000
    today = date(2026, 2, 15)
    reserve_tokens(db_session, workspace_id, 200, today)

    result = commit_usage(db_session, workspace_id, 125, today)

    assert result["used_now"] == 125
    assert result["reserved_now"] == 75
    status = get_budget_status(db_session, workspace_id, today)
    assert status["used"] == 125
    assert status["reserved"] == 75
    assert status["remaining"] == 800


def test_release_reduces_reserved(db_session: Session, workspace_id: uuid.UUID, limit_guard) -> None:
    settings.DAILY_TOKEN_LIMIT = 1_000
    today = date(2026, 2, 15)
    reserve_tokens(db_session, workspace_id, 150, today)

    result = release_tokens(db_session, workspace_id, 50, today)

    assert result["reserved_now"] == 100
    status = get_budget_status(db_session, workspace_id, today)
    assert status["reserved"] == 100
    assert status["used"] == 0


def test_budget_operations_use_short_transaction_when_caller_session_is_active(
    sqlite_session_factory: sessionmaker,
    limit_guard,
) -> None:
    settings.DAILY_TOKEN_LIMIT = 1_000
    today = date(2026, 2, 15)

    setup_session = sqlite_session_factory()
    workspace = Workspace(name="Active Transaction", owner_id=uuid.uuid4())
    setup_session.add(workspace)
    setup_session.commit()
    setup_session.close()

    caller_session = sqlite_session_factory()
    observer_session = sqlite_session_factory()
    try:
        # Trigger SQLAlchemy autobegin on the caller session before reserving tokens.
        caller_session.get(Workspace, workspace.id)

        reserve_result = reserve_tokens(caller_session, workspace.id, 200, today)
        status_after_reserve = get_budget_status(observer_session, workspace.id, today)

        commit_result = commit_usage(caller_session, workspace.id, 125, today)
        status_after_commit = get_budget_status(observer_session, workspace.id, today)

        release_result = release_tokens(caller_session, workspace.id, 75, today)
        status_after_release = get_budget_status(observer_session, workspace.id, today)

        assert reserve_result["reserved"] == 200
        assert status_after_reserve["reserved"] == 200
        assert commit_result["used_now"] == 125
        assert status_after_commit["used"] == 125
        assert status_after_commit["reserved"] == 75
        assert release_result["reserved_now"] == 0
        assert status_after_release["used"] == 125
        assert status_after_release["reserved"] == 0
    finally:
        caller_session.close()
        observer_session.close()


def test_resets_at_is_next_midnight_utc(db_session: Session, workspace_id: uuid.UUID, limit_guard) -> None:
    settings.DAILY_TOKEN_LIMIT = 1_000
    usage_day = date(2026, 2, 15)

    status = get_budget_status(db_session, workspace_id, usage_day)
    resets_at = status["resets_at"]

    assert isinstance(resets_at, datetime)
    assert resets_at == datetime(2026, 2, 16, 0, 0, 0, tzinfo=UTC)


@pytest.mark.skipif(not bool(os.getenv("TEST_DATABASE_URL")), reason="requires TEST_DATABASE_URL")
def test_concurrent_reservation_safety_postgres(pg_session_factory: sessionmaker | None, limit_guard) -> None:
    assert pg_session_factory is not None
    settings.DAILY_TOKEN_LIMIT = 100
    usage_day = date(2026, 2, 15)

    setup_session = pg_session_factory()
    workspace = Workspace(name="Concurrent", owner_id=uuid.uuid4())
    setup_session.add(workspace)
    setup_session.commit()
    setup_session.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[Exception] = []

    def _reserve() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            reserve_tokens(session, workspace.id, 60, usage_day)
            outcomes.append("ok")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()

    t1 = threading.Thread(target=_reserve)
    t2 = threading.Thread(target=_reserve)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    ok_count = sum(1 for value in outcomes if value == "ok")
    budget_errors = [error for error in errors if isinstance(error, BudgetExceededError)]
    assert ok_count == 1
    assert len(budget_errors) == 1
