from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_workspace_id
from app.core.auth import AuthenticatedUser
from app.db.session import get_db
from app.schemas.chat import (
    ChatSessionCreateRequest,
    ChatSessionDetailResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
    ChatSessionMetadata,
    ChatSessionUpdateRequest,
)

router = APIRouter()
QUERY_LOG_CHAT_MARKER = "__CHAT_SESSION__"


def _normalize_title(title: str | None, messages: list[dict[str, object]]) -> str:
    if title and title.strip():
        return title.strip()[:200]

    for message in messages:
        if message.get("role") == "user":
            content = str(message.get("content") or "").strip()
            if content:
                return content[:120]

    return "Untitled chat"


def _selected_document_ids(
    document_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
) -> list[uuid.UUID]:
    selected: list[uuid.UUID] = []
    if document_ids:
        selected.extend(document_ids)
    if document_id is not None and document_id not in selected:
        selected.insert(0, document_id)
    return selected


def _ensure_documents_in_workspace(
    db: Session, workspace_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> None:
    if not document_ids:
        return

    rows = (
        db.execute(
            text("""
            SELECT id
            FROM documents
            WHERE id IN :document_ids
              AND workspace_id = :workspace_id
            """).bindparams(bindparam("document_ids", expanding=True)),
            {"document_ids": document_ids, "workspace_id": workspace_id},
        )
        .scalars()
        .all()
    )
    found = set(rows)
    missing = [document_id for document_id in document_ids if document_id not in found]
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


def _ensure_document_in_workspace(
    db: Session, workspace_id: uuid.UUID, document_id: uuid.UUID | None
) -> None:
    _ensure_documents_in_workspace(db, workspace_id, [document_id] if document_id else [])


def _chat_sessions_table_exists(db: Session) -> bool:
    exists = db.execute(text("SELECT to_regclass('public.chat_sessions')")).scalar_one_or_none()
    return exists is not None


def _build_payload(
    *,
    messages: list[dict[str, object]],
    started_at: datetime,
    ended_at: datetime | None,
) -> str:
    return json.dumps(
        {
            "messages": messages,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat() if ended_at else None,
        }
    )


def _parse_payload(
    raw: str | None, fallback_started_at: datetime
) -> tuple[list[dict[str, object]], datetime, datetime | None]:
    if not raw:
        return [], fallback_started_at, None

    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], fallback_started_at, None

    if isinstance(data, list):
        return data, fallback_started_at, None

    if not isinstance(data, dict):
        return [], fallback_started_at, None

    messages = data.get("messages")
    started_raw = data.get("started_at")
    ended_raw = data.get("ended_at")

    started_at = fallback_started_at
    ended_at = None
    if isinstance(started_raw, str):
        try:
            started_at = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
        except ValueError:
            started_at = fallback_started_at
    if isinstance(ended_raw, str):
        try:
            ended_at = datetime.fromisoformat(ended_raw.replace("Z", "+00:00"))
        except ValueError:
            ended_at = None

    if not isinstance(messages, list):
        return [], started_at, ended_at
    return messages, started_at, ended_at


@router.post("/sessions", response_model=ChatSessionMetadata)
def create_chat_session(
    payload: ChatSessionCreateRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatSessionMetadata:
    document_ids = _selected_document_ids(payload.document_id, payload.document_ids)
    primary_document_id = document_ids[0] if document_ids else None
    _ensure_documents_in_workspace(db, workspace_id, document_ids)
    messages = [message.model_dump(mode="json") for message in payload.messages]
    title = _normalize_title(payload.title, messages)
    now_utc = datetime.now(UTC)

    if not _chat_sessions_table_exists(db):
        row = (
            db.execute(
                text("""
                INSERT INTO query_logs (
                    workspace_id,
                    user_id,
                    query_text,
                    documents_searched,
                    retrieved_chunk_ids,
                    chunk_scores,
                    answer_text,
                    error_message,
                    retrieval_latency_ms,
                    llm_latency_ms,
                    total_latency_ms,
                    embedding_tokens_used,
                    llm_input_tokens,
                    llm_output_tokens,
                    total_tokens_used,
                    created_at
                ) VALUES (
                    :workspace_id,
                    :user_id,
                    :query_text,
                    :documents_searched,
                    :retrieved_chunk_ids,
                    :chunk_scores,
                    :answer_text,
                    :error_message,
                    0,
                    NULL,
                    0,
                    0,
                    NULL,
                    NULL,
                    0,
                    NOW()
                )
                RETURNING id, query_text AS title, created_at
                """),
                {
                    "workspace_id": workspace_id,
                    "user_id": user.user_id,
                    "query_text": title,
                    "documents_searched": document_ids,
                    "retrieved_chunk_ids": [],
                    "chunk_scores": [],
                    "answer_text": _build_payload(
                        messages=messages, started_at=now_utc, ended_at=None
                    ),
                    "error_message": QUERY_LOG_CHAT_MARKER,
                },
            )
            .mappings()
            .one()
        )
        db.commit()
        return ChatSessionMetadata(
            id=row["id"],
            title=row["title"],
            document_id=primary_document_id,
            document_ids=document_ids,
            created_at=row["created_at"],
            updated_at=row["created_at"],
            ended_at=None,
        )

    row = (
        db.execute(
            text("""
            INSERT INTO chat_sessions (
                workspace_id,
                document_id,
                document_ids,
                title,
                messages,
                started_at,
                created_at,
                updated_at
            )
            VALUES (:workspace_id, :document_id, :document_ids, :title, :messages, NOW(), NOW(), NOW())
            RETURNING id, title, document_id, document_ids, created_at, updated_at, ended_at
            """).bindparams(bindparam("messages", type_=JSONB)),
            {
                "workspace_id": workspace_id,
                "document_id": primary_document_id,
                "document_ids": document_ids,
                "title": title,
                "messages": messages,
            },
        )
        .mappings()
        .one()
    )
    db.commit()

    return ChatSessionMetadata(
        id=row["id"],
        title=row["title"],
        document_id=row["document_id"],
        document_ids=list(row["document_ids"] or []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        ended_at=row["ended_at"],
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionMetadata)
def update_chat_session(
    session_id: uuid.UUID,
    payload: ChatSessionUpdateRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> ChatSessionMetadata:
    if not _chat_sessions_table_exists(db):
        existing = (
            db.execute(
                text("""
                SELECT id, query_text, documents_searched, answer_text, created_at
                FROM query_logs
                WHERE id = :session_id
                  AND workspace_id = :workspace_id
                  AND error_message = :marker
                LIMIT 1
                """),
                {
                    "session_id": session_id,
                    "workspace_id": workspace_id,
                    "marker": QUERY_LOG_CHAT_MARKER,
                },
            )
            .mappings()
            .first()
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
            )

        old_messages, started_at, old_ended_at = _parse_payload(
            existing["answer_text"], existing["created_at"]
        )
        messages = (
            [message.model_dump(mode="json") for message in payload.messages]
            if payload.messages is not None
            else old_messages
        )
        title = _normalize_title(payload.title, messages)
        ended_at = datetime.now(UTC) if payload.ended else old_ended_at
        old_document_ids = list(existing["documents_searched"] or [])
        document_ids = (
            _selected_document_ids(payload.document_id, payload.document_ids) or old_document_ids
        )
        _ensure_documents_in_workspace(db, workspace_id, document_ids)

        row = (
            db.execute(
                text("""
                UPDATE query_logs
                SET query_text = :query_text,
                    documents_searched = :documents_searched,
                    answer_text = :answer_text
                WHERE id = :session_id
                  AND workspace_id = :workspace_id
                  AND error_message = :marker
                RETURNING id, query_text AS title, documents_searched, created_at
                """),
                {
                    "session_id": session_id,
                    "workspace_id": workspace_id,
                    "marker": QUERY_LOG_CHAT_MARKER,
                    "query_text": title,
                    "documents_searched": document_ids,
                    "answer_text": _build_payload(
                        messages=messages, started_at=started_at, ended_at=ended_at
                    ),
                },
            )
            .mappings()
            .one()
        )
        db.commit()

        docs = list(row["documents_searched"] or [])
        return ChatSessionMetadata(
            id=row["id"],
            title=row["title"],
            document_id=docs[0] if docs else None,
            document_ids=docs,
            created_at=row["created_at"],
            updated_at=datetime.now(UTC),
            ended_at=ended_at,
        )

    existing = (
        db.execute(
            text("""
            SELECT id, document_id, document_ids, messages
            FROM chat_sessions
            WHERE id = :session_id
              AND workspace_id = :workspace_id
            LIMIT 1
            """),
            {"session_id": session_id, "workspace_id": workspace_id},
        )
        .mappings()
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    messages = (
        [message.model_dump(mode="json") for message in payload.messages]
        if payload.messages is not None
        else existing["messages"]
    )
    title = _normalize_title(payload.title, messages)
    document_ids = _selected_document_ids(payload.document_id, payload.document_ids) or list(
        existing["document_ids"] or []
    )
    if not document_ids and existing["document_id"]:
        document_ids = [existing["document_id"]]
    primary_document_id = document_ids[0] if document_ids else None
    _ensure_documents_in_workspace(db, workspace_id, document_ids)

    row = (
        db.execute(
            text("""
            UPDATE chat_sessions
            SET title = :title,
                document_id = :document_id,
                document_ids = :document_ids,
                messages = :messages,
                ended_at = CASE WHEN :ended THEN NOW() ELSE ended_at END,
                updated_at = NOW()
            WHERE id = :session_id
              AND workspace_id = :workspace_id
            RETURNING id, title, document_id, document_ids, created_at, updated_at, ended_at
            """).bindparams(bindparam("messages", type_=JSONB)),
            {
                "session_id": session_id,
                "workspace_id": workspace_id,
                "title": title,
                "document_id": primary_document_id,
                "document_ids": document_ids,
                "messages": messages,
                "ended": payload.ended,
            },
        )
        .mappings()
        .one()
    )
    db.commit()

    return ChatSessionMetadata(
        id=row["id"],
        title=row["title"],
        document_id=row["document_id"],
        document_ids=list(row["document_ids"] or []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        ended_at=row["ended_at"],
    )


@router.get("/sessions", response_model=ChatSessionListResponse)
def list_chat_sessions(
    document_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20),
    offset: int = Query(default=0),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> ChatSessionListResponse:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 100",
        )
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0")

    _ensure_document_in_workspace(db, workspace_id, document_id)

    if not _chat_sessions_table_exists(db):
        where_sql = "workspace_id = :workspace_id AND error_message = :marker"
        params: dict[str, object] = {
            "workspace_id": workspace_id,
            "document_id": document_id,
            "limit": limit,
            "offset": offset,
            "marker": QUERY_LOG_CHAT_MARKER,
        }
        if document_id is not None:
            where_sql += " AND :document_id = ANY(documents_searched)"

        total = int(
            db.execute(
                text(f"SELECT COUNT(*) FROM query_logs WHERE {where_sql}"),
                params,
            ).scalar_one()
            or 0
        )
        rows = (
            db.execute(
                text(f"""
                SELECT id, query_text AS title, documents_searched, answer_text, created_at
                FROM query_logs
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """),
                params,
            )
            .mappings()
            .all()
        )

        items: list[ChatSessionListItem] = []
        for row in rows:
            docs = list(row["documents_searched"] or [])
            _, _, ended_at = _parse_payload(row["answer_text"], row["created_at"])
            items.append(
                ChatSessionListItem(
                    id=row["id"],
                    title=row["title"],
                    document_id=docs[0] if docs else None,
                    document_ids=docs,
                    updated_at=row["created_at"],
                    ended_at=ended_at,
                )
            )

        return ChatSessionListResponse(items=items, total=total)

    where_sql = "workspace_id = :workspace_id"
    params: dict[str, object] = {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "limit": limit,
        "offset": offset,
    }
    if document_id is not None:
        where_sql += " AND (:document_id = ANY(document_ids) OR document_id = :document_id)"

    total = int(
        db.execute(
            text(f"SELECT COUNT(*) FROM chat_sessions WHERE {where_sql}"),
            params,
        ).scalar_one()
        or 0
    )
    rows = (
        db.execute(
            text(f"""
            SELECT id, title, document_id, document_ids, updated_at, ended_at
            FROM chat_sessions
            WHERE {where_sql}
            ORDER BY updated_at DESC
            LIMIT :limit OFFSET :offset
            """),
            params,
        )
        .mappings()
        .all()
    )

    return ChatSessionListResponse(
        items=[
            ChatSessionListItem(
                id=row["id"],
                title=row["title"],
                document_id=row["document_id"],
                document_ids=list(
                    row["document_ids"] or ([row["document_id"]] if row["document_id"] else [])
                ),
                updated_at=row["updated_at"],
                ended_at=row["ended_at"],
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailResponse)
def get_chat_session(
    session_id: uuid.UUID,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    db: Session = Depends(get_db),
) -> ChatSessionDetailResponse:
    if not _chat_sessions_table_exists(db):
        row = (
            db.execute(
                text("""
                SELECT id, query_text AS title, documents_searched, answer_text, created_at
                FROM query_logs
                WHERE id = :session_id
                  AND workspace_id = :workspace_id
                  AND error_message = :marker
                LIMIT 1
                """),
                {
                    "session_id": session_id,
                    "workspace_id": workspace_id,
                    "marker": QUERY_LOG_CHAT_MARKER,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
            )

        docs = list(row["documents_searched"] or [])
        messages, started_at, ended_at = _parse_payload(row["answer_text"], row["created_at"])
        return ChatSessionDetailResponse(
            id=row["id"],
            title=row["title"],
            document_id=docs[0] if docs else None,
            document_ids=docs,
            messages=messages,
            started_at=started_at,
            ended_at=ended_at,
        )

    row = (
        db.execute(
            text("""
            SELECT id, title, document_id, document_ids, messages, started_at, ended_at
            FROM chat_sessions
            WHERE id = :session_id
              AND workspace_id = :workspace_id
            LIMIT 1
            """),
            {"session_id": session_id, "workspace_id": workspace_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    return ChatSessionDetailResponse(
        id=row["id"],
        title=row["title"],
        document_id=row["document_id"],
        document_ids=list(
            row["document_ids"] or ([row["document_id"]] if row["document_id"] else [])
        ),
        messages=row["messages"] or [],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )
