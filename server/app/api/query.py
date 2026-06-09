from __future__ import annotations

import math
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_workspace_id
from app.config import settings, utc_next_reset_at
from app.core.auth import AuthenticatedUser
from app.core.embeddings import embed_query_text
from app.core.errors import BudgetExceededError
from app.core.llm import answer_question_strict_grounded
from app.core.prompts import INSUFFICIENT_CONTEXT_MESSAGE
from app.core.rate_limit import enforce_query_rate_limit
from app.core.retrieval import RetrievedChunk, retrieve_top_k_chunks_for_documents
from app.core.token_budget import commit_usage, get_budget_status, release_tokens, reserve_tokens
from app.db.session import get_db
from app.schemas.query import QueryCitation, QueryRequest, QueryResponse, QueryUsage

router = APIRouter()
PROMPT_TEMPLATE_TOKENS = 200
QUERY_READY_STATUSES = {"ready", "indexed"}


def _enforce_query_rate_limit(workspace_id: uuid.UUID) -> None:
    enforce_query_rate_limit(workspace_id)


def _estimate_query_tokens(question: str) -> int:
    return int(math.ceil((len(question) / 4) * 1.3))


def _estimate_llm_input_tokens(question: str, chunks: list[RetrievedChunk]) -> int:
    return int(
        math.ceil(
            sum(chunk.token_count for chunk in chunks)
            + PROMPT_TEMPLATE_TOKENS
            + (len(question) / 4)
        )
    )


def _log_query(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: str,
    document_ids: list[uuid.UUID],
    question: str,
    retrieved_chunks: list[RetrievedChunk],
    answer_text: str | None,
    error_message: str | None,
    retrieval_latency_ms: int,
    llm_latency_ms: int | None,
    total_latency_ms: int,
    embedding_tokens_used: int,
    llm_input_tokens: int | None,
    llm_output_tokens: int | None,
    total_tokens_used: int,
) -> None:
    if not settings.LOG_EACH_QUERY:
        return

    sql = text(
        """
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
            :retrieval_latency_ms,
            :llm_latency_ms,
            :total_latency_ms,
            :embedding_tokens_used,
            :llm_input_tokens,
            :llm_output_tokens,
            :total_tokens_used,
            NOW()
        )
        """
    )
    db.execute(
        sql,
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "query_text": question,
            "documents_searched": document_ids,
            "retrieved_chunk_ids": [chunk.chunk_id for chunk in retrieved_chunks],
            "chunk_scores": [chunk.score for chunk in retrieved_chunks],
            "answer_text": answer_text,
            "error_message": error_message,
            "retrieval_latency_ms": retrieval_latency_ms,
            "llm_latency_ms": llm_latency_ms,
            "total_latency_ms": total_latency_ms,
            "embedding_tokens_used": embedding_tokens_used,
            "llm_input_tokens": llm_input_tokens,
            "llm_output_tokens": llm_output_tokens,
            "total_tokens_used": total_tokens_used,
        },
    )
    db.commit()


def _usage_to_response(usage: dict[str, int | datetime]) -> QueryUsage:
    return QueryUsage(
        limit=int(usage["limit"]),
        used=int(usage["used"]),
        reserved=int(usage["reserved"]),
        remaining=int(usage["remaining"]),
        resets_at=usage["resets_at"],
    )


def _selected_document_ids(payload: QueryRequest) -> list[uuid.UUID]:
    selected: list[uuid.UUID] = []
    for document_id in payload.selected_document_ids:
        if document_id not in selected:
            selected.append(document_id)
    return selected


def resolve_query_document_ids(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    payload: QueryRequest,
) -> list[uuid.UUID]:
    document_ids = _selected_document_ids(payload)
    if not document_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one document is required",
        )
    if len(document_ids) > settings.MAX_QUERY_DOCUMENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query supports up to {settings.MAX_QUERY_DOCUMENTS} selected documents",
        )

    rows = (
        db.execute(
            text(
                """
                SELECT id, status, COALESCE(page_count, 0) AS page_count
                FROM documents
                WHERE workspace_id = :workspace_id
                  AND id IN :document_ids
                """
            ).bindparams(bindparam("document_ids", expanding=True)),
            {"workspace_id": workspace_id, "document_ids": document_ids},
        )
        .mappings()
        .all()
    )
    rows_by_id = {row["id"]: row for row in rows}
    missing_ids = [document_id for document_id in document_ids if document_id not in rows_by_id]
    if missing_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    not_ready = [
        document_id
        for document_id in document_ids
        if rows_by_id[document_id]["status"] not in QUERY_READY_STATUSES
    ]
    if not_ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="All selected documents must be ready or indexed for querying",
        )

    total_pages = sum(int(row["page_count"] or 0) for row in rows)
    if total_pages > settings.MAX_QUERY_TOTAL_PAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Selected documents contain {total_pages} pages; "
                f"query supports up to {settings.MAX_QUERY_TOTAL_PAGES} pages"
            ),
        )

    return document_ids


@router.post("", response_model=QueryResponse)
def run_query(
    payload: QueryRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QueryResponse:
    _enforce_query_rate_limit(workspace_id)
    question = payload.question.strip()
    if not question or len(question) > settings.MAX_QUESTION_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"question must be between 1 and {settings.MAX_QUESTION_CHARS} characters",
        )

    document_ids = resolve_query_document_ids(db=db, workspace_id=workspace_id, payload=payload)

    request_started = time.perf_counter()
    usage_date_utc = datetime.now(UTC)
    reserved_amount = 0
    answer_text: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_latency_ms: int | None = None

    try:
        retrieval_started = time.perf_counter()
        embedding_result = embed_query_text(question)
        chunks = retrieve_top_k_chunks_for_documents(
            db=db,
            workspace_id=workspace_id,
            document_ids=document_ids,
            query_embedding=embedding_result.embedding,
            top_k=settings.TOP_K,
        )
        retrieval_latency_ms = int((time.perf_counter() - retrieval_started) * 1000)

        estimated_query_embedding = _estimate_query_tokens(question)
        estimated_input = _estimate_llm_input_tokens(question, chunks)
        estimated_total = (
            estimated_query_embedding + estimated_input + settings.LLM_MAX_OUTPUT_TOKENS
        )
        reserve_tokens(
            db=db,
            workspace_id=workspace_id,
            amount=estimated_total,
            usage_date_utc=usage_date_utc,
            reservation_ttl_seconds=settings.RESERVATION_TTL_SECONDS,
        )
        reserved_amount = estimated_total

        if not chunks:
            answer_text = INSUFFICIENT_CONTEXT_MESSAGE
            committed = min(embedding_result.total_tokens, reserved_amount)
            commit_usage(
                db=db, workspace_id=workspace_id, amount=committed, usage_date_utc=usage_date_utc
            )
            if reserved_amount > committed:
                release_tokens(
                    db=db,
                    workspace_id=workspace_id,
                    amount=reserved_amount - committed,
                    usage_date_utc=usage_date_utc,
                )
            reserved_amount = 0
            usage_now = get_budget_status(
                db=db, workspace_id=workspace_id, usage_date_utc=usage_date_utc
            )
            total_latency_ms = int((time.perf_counter() - request_started) * 1000)
            _log_query(
                db=db,
                workspace_id=workspace_id,
                user_id=user.user_id,
                document_ids=document_ids,
                question=question,
                retrieved_chunks=chunks,
                answer_text=answer_text,
                error_message=None,
                retrieval_latency_ms=retrieval_latency_ms,
                llm_latency_ms=None,
                total_latency_ms=total_latency_ms,
                embedding_tokens_used=embedding_result.total_tokens,
                llm_input_tokens=None,
                llm_output_tokens=None,
                total_tokens_used=committed,
            )
            return QueryResponse(
                answer=answer_text, citations=[], usage=_usage_to_response(usage_now)
            )

        llm_started = time.perf_counter()
        llm_result = answer_question_strict_grounded(question=question, chunks=chunks)
        llm_latency_ms = int((time.perf_counter() - llm_started) * 1000)
        answer_text = llm_result.answer or INSUFFICIENT_CONTEXT_MESSAGE
        llm_input_tokens = llm_result.input_tokens
        llm_output_tokens = llm_result.output_tokens

        actual_total = embedding_result.total_tokens + llm_result.total_tokens
        committed = min(actual_total, reserved_amount)
        commit_usage(
            db=db, workspace_id=workspace_id, amount=committed, usage_date_utc=usage_date_utc
        )
        if reserved_amount > committed:
            release_tokens(
                db=db,
                workspace_id=workspace_id,
                amount=reserved_amount - committed,
                usage_date_utc=usage_date_utc,
            )
        reserved_amount = 0
        usage_now = get_budget_status(
            db=db, workspace_id=workspace_id, usage_date_utc=usage_date_utc
        )

        citations = [
            QueryCitation(
                document_id=chunk.document_id,
                page_number=chunk.page_number,
                chunk_id=chunk.chunk_id,
                score=chunk.score,
                snippet=chunk.snippet,
            )
            for chunk in chunks
        ]
        total_latency_ms = int((time.perf_counter() - request_started) * 1000)
        _log_query(
            db=db,
            workspace_id=workspace_id,
            user_id=user.user_id,
            document_ids=document_ids,
            question=question,
            retrieved_chunks=chunks,
            answer_text=answer_text,
            error_message=None,
            retrieval_latency_ms=retrieval_latency_ms,
            llm_latency_ms=llm_latency_ms,
            total_latency_ms=total_latency_ms,
            embedding_tokens_used=embedding_result.total_tokens,
            llm_input_tokens=llm_input_tokens,
            llm_output_tokens=llm_output_tokens,
            total_tokens_used=committed,
        )
        return QueryResponse(
            answer=answer_text,
            citations=citations,
            usage=_usage_to_response(usage_now),
        )
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": {
                    "code": "BUDGET_EXCEEDED",
                    "message": "Daily token limit reached for this workspace",
                    "details": {
                        "used": exc.used,
                        "reserved": exc.reserved,
                        "limit": exc.limit,
                        "remaining": exc.remaining,
                        "resets_at": utc_next_reset_at(usage_date_utc),
                    },
                }
            },
        ) from exc
    except HTTPException:
        if reserved_amount > 0:
            release_tokens(
                db=db,
                workspace_id=workspace_id,
                amount=reserved_amount,
                usage_date_utc=usage_date_utc,
            )
        raise
    except Exception as exc:  # noqa: BLE001
        if reserved_amount > 0:
            release_tokens(
                db=db,
                workspace_id=workspace_id,
                amount=reserved_amount,
                usage_date_utc=usage_date_utc,
            )
        total_latency_ms = int((time.perf_counter() - request_started) * 1000)
        try:
            _log_query(
                db=db,
                workspace_id=workspace_id,
                user_id=user.user_id,
                document_ids=document_ids,
                question=question,
                retrieved_chunks=[],
                answer_text=answer_text,
                error_message=str(exc),
                retrieval_latency_ms=0,
                llm_latency_ms=llm_latency_ms,
                total_latency_ms=total_latency_ms,
                embedding_tokens_used=0,
                llm_input_tokens=llm_input_tokens,
                llm_output_tokens=llm_output_tokens,
                total_tokens_used=0,
            )
        except Exception:  # noqa: BLE001
            db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {exc}",
        ) from exc
