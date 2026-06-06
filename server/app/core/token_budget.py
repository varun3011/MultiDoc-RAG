from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime, time, timedelta
from typing import Callable, TypeVar
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.core.errors import BudgetExceededError, InvalidReservationError
from app.db.models import WorkspaceDailyUsage

T = TypeVar("T")


def _as_utc_date(usage_date_utc: date | datetime) -> date:
    if isinstance(usage_date_utc, datetime):
        return usage_date_utc.astimezone(UTC).date()
    return usage_date_utc


def _next_reset_at(usage_date: date) -> datetime:
    return datetime.combine(usage_date + timedelta(days=1), time.min, tzinfo=UTC)


def _ensure_non_negative_amount(amount: int, field_name: str = "amount") -> int:
    if not isinstance(amount, int):
        raise InvalidReservationError(f"{field_name} must be an integer")
    if amount < 0:
        raise InvalidReservationError(f"{field_name} must be >= 0")
    return amount


def _insert_usage_row_if_missing(db: Session, workspace_id: uuid.UUID, usage_date: date) -> None:
    dialect = db.get_bind().dialect.name
    values = {
        "workspace_id": workspace_id,
        "date": usage_date,
        "tokens_used": 0,
        "tokens_reserved": 0,
    }
    if dialect == "postgresql":
        stmt = pg_insert(WorkspaceDailyUsage).values(**values).on_conflict_do_nothing(
            index_elements=["workspace_id", "date"]
        )
        db.execute(stmt)
        return
    if dialect == "sqlite":
        stmt = sqlite_insert(WorkspaceDailyUsage).values(**values).on_conflict_do_nothing(
            index_elements=["workspace_id", "date"]
        )
        db.execute(stmt)
        return

    # Generic fallback for other dialects.
    existing = db.execute(
        select(WorkspaceDailyUsage.workspace_id).where(
            WorkspaceDailyUsage.workspace_id == workspace_id,
            WorkspaceDailyUsage.date == usage_date,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(WorkspaceDailyUsage(**values))
        db.flush()


def _transaction_context(db: Session):
    if db.in_transaction():
        return nullcontext()
    return db.begin()


def _run_in_isolated_session(db: Session, operation: Callable[[Session], T]) -> T:
    isolated_factory = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
    isolated_db = isolated_factory()
    try:
        result = operation(isolated_db)
        isolated_db.commit()
        return result
    except Exception:
        isolated_db.rollback()
        raise
    finally:
        isolated_db.close()


def get_or_create_daily_row(db: Session, workspace_id: uuid.UUID, usage_date_utc: date | datetime) -> WorkspaceDailyUsage:
    usage_date = _as_utc_date(usage_date_utc)
    _insert_usage_row_if_missing(db, workspace_id, usage_date)
    stmt = (
        select(WorkspaceDailyUsage)
        .where(
            WorkspaceDailyUsage.workspace_id == workspace_id,
            WorkspaceDailyUsage.date == usage_date,
        )
        .with_for_update()
    )
    return db.execute(stmt).scalar_one()


def _reserve_tokens_in_session(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    usage_date_utc: date | datetime,
) -> dict[str, int]:
    token_limit = int(settings.DAILY_TOKEN_LIMIT)

    with _transaction_context(db):
        row = get_or_create_daily_row(db, workspace_id, usage_date_utc)
        used = int(row.tokens_used)
        reserved = int(row.tokens_reserved)
        remaining = token_limit - (used + reserved)
        if amount > remaining:
            raise BudgetExceededError(
                "Daily token limit reached for this workspace",
                used=used,
                reserved=reserved,
                limit=token_limit,
            )

        row.tokens_reserved = reserved + amount
        db.flush()
        reserved_now = int(row.tokens_reserved)
        remaining_now = max(0, token_limit - (used + reserved_now))

    return {
        "reserved": reserved_now,
        "remaining": remaining_now,
        "limit": token_limit,
    }


def _release_tokens_in_session(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    usage_date_utc: date | datetime,
) -> dict[str, int]:
    with _transaction_context(db):
        row = get_or_create_daily_row(db, workspace_id, usage_date_utc)
        reserved = int(row.tokens_reserved)
        if amount > reserved:
            raise InvalidReservationError("Cannot release more tokens than currently reserved")
        row.tokens_reserved = reserved - amount
        db.flush()
        reserved_now = int(row.tokens_reserved)

    return {"reserved_now": reserved_now}


def _commit_usage_in_session(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    usage_date_utc: date | datetime,
) -> dict[str, int]:
    with _transaction_context(db):
        row = get_or_create_daily_row(db, workspace_id, usage_date_utc)
        reserved = int(row.tokens_reserved)
        used = int(row.tokens_used)
        if amount > reserved:
            raise InvalidReservationError("Cannot commit more tokens than currently reserved")
        row.tokens_reserved = reserved - amount
        row.tokens_used = used + amount
        db.flush()
        used_now = int(row.tokens_used)
        reserved_now = int(row.tokens_reserved)

    return {"used_now": used_now, "reserved_now": reserved_now}


def reserve_tokens(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    usage_date_utc: date | datetime,
    reservation_ttl_seconds: int = 600,
) -> dict[str, int]:
    reservation_amount = _ensure_non_negative_amount(amount)
    _ensure_non_negative_amount(reservation_ttl_seconds, "reservation_ttl_seconds")
    if db.in_transaction():
        return _run_in_isolated_session(
            db,
            lambda isolated_db: _reserve_tokens_in_session(
                isolated_db,
                workspace_id=workspace_id,
                amount=reservation_amount,
                usage_date_utc=usage_date_utc,
            ),
        )
    return _reserve_tokens_in_session(
        db,
        workspace_id=workspace_id,
        amount=reservation_amount,
        usage_date_utc=usage_date_utc,
    )


def release_tokens(db: Session, workspace_id: uuid.UUID, amount: int, usage_date_utc: date | datetime) -> dict[str, int]:
    release_amount = _ensure_non_negative_amount(amount)
    if db.in_transaction():
        return _run_in_isolated_session(
            db,
            lambda isolated_db: _release_tokens_in_session(
                isolated_db,
                workspace_id=workspace_id,
                amount=release_amount,
                usage_date_utc=usage_date_utc,
            ),
        )
    return _release_tokens_in_session(
        db,
        workspace_id=workspace_id,
        amount=release_amount,
        usage_date_utc=usage_date_utc,
    )


def commit_usage(db: Session, workspace_id: uuid.UUID, amount: int, usage_date_utc: date | datetime) -> dict[str, int]:
    usage_amount = _ensure_non_negative_amount(amount)
    if db.in_transaction():
        return _run_in_isolated_session(
            db,
            lambda isolated_db: _commit_usage_in_session(
                isolated_db,
                workspace_id=workspace_id,
                amount=usage_amount,
                usage_date_utc=usage_date_utc,
            ),
        )
    return _commit_usage_in_session(
        db,
        workspace_id=workspace_id,
        amount=usage_amount,
        usage_date_utc=usage_date_utc,
    )


def get_budget_status(
    db: Session,
    workspace_id: uuid.UUID,
    usage_date_utc: date | datetime,
) -> dict[str, int | datetime]:
    usage_date = _as_utc_date(usage_date_utc)
    token_limit = int(settings.DAILY_TOKEN_LIMIT)

    # Read-only status lookup for UI endpoints; avoid row creation/locking here
    # to prevent timeout under concurrent token reservations.
    row = db.execute(
        select(WorkspaceDailyUsage).where(
            WorkspaceDailyUsage.workspace_id == workspace_id,
            WorkspaceDailyUsage.date == usage_date,
        )
    ).scalar_one_or_none()
    if row is None:
        used = 0
        reserved = 0
    else:
        used = int(row.tokens_used)
        reserved = int(row.tokens_reserved)
    remaining = max(0, token_limit - (used + reserved))

    return {
        "used": used,
        "reserved": reserved,
        "limit": token_limit,
        "remaining": remaining,
        "resets_at": _next_reset_at(usage_date),
    }
