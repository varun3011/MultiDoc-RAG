from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_workspace_id
from app.api.query import (
    _enforce_query_rate_limit,
    _estimate_llm_input_tokens,
    _estimate_query_tokens,
    _log_query,
    resolve_query_document_ids,
)
from app.config import settings, utc_next_reset_at
from app.core.auth import AuthenticatedUser
from app.core.embeddings import embed_query_text
from app.core.errors import BudgetExceededError
from app.core.llm import LLMResult, stream_answer_question_strict_grounded
from app.core.prompts import INSUFFICIENT_CONTEXT_MESSAGE
from app.core.retrieval import RetrievedChunk, retrieve_top_k_chunks_for_documents
from app.core.token_budget import commit_usage, get_budget_status, release_tokens, reserve_tokens
from app.db.session import get_db
from app.schemas.query import QueryRequest

router = APIRouter()


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _usage_payload(usage: dict[str, int | datetime]) -> dict[str, object]:
    return {
        "usage": {
            "limit": int(usage["limit"]),
            "used": int(usage["used"]),
            "reserved": int(usage["reserved"]),
            "remaining": int(usage["remaining"]),
            "resets_at": usage["resets_at"].isoformat(),
        }
    }


def _error_payload(message: str, code: str) -> dict[str, str]:
    return {"message": message, "code": code}


@router.post("/stream")
async def run_query_stream(
    payload: QueryRequest,
    request: Request,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    async def event_stream():
        request_id = str(uuid.uuid4())
        request_started = time.perf_counter()
        usage_date_utc = datetime.now(UTC)
        reserved_amount = 0
        retrieval_latency_ms = 0
        llm_latency_ms: int | None = None
        llm_input_tokens: int | None = None
        llm_output_tokens: int | None = None
        embedding_tokens_used = 0
        chunks: list[RetrievedChunk] = []
        answer_text: str | None = None

        try:
            _enforce_query_rate_limit(workspace_id)
            question = payload.question.strip()
            if not question or len(question) > settings.MAX_QUESTION_CHARS:
                yield _sse_event(
                    "error",
                    _error_payload(
                        f"question must be between 1 and {settings.MAX_QUESTION_CHARS} characters",
                        "INVALID_QUESTION",
                    ),
                )
                return

            try:
                document_ids = resolve_query_document_ids(
                    db=db, workspace_id=workspace_id, payload=payload
                )
            except HTTPException as exc:
                code = (
                    "DOCUMENT_NOT_FOUND"
                    if exc.status_code == 404
                    else (
                        "DOCUMENT_NOT_READY"
                        if exc.status_code == 409
                        else "INVALID_DOCUMENT_SELECTION"
                    )
                )
                message = (
                    str(exc.detail) if isinstance(exc.detail, str) else "Invalid document selection"
                )
                yield _sse_event("error", _error_payload(message, code))
                return

            retrieval_started = time.perf_counter()
            embedding_result = embed_query_text(question)
            embedding_tokens_used = embedding_result.total_tokens
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

            yield _sse_event(
                "meta",
                {
                    "request_id": request_id,
                    "document_id": str(document_ids[0]),
                    "document_ids": [str(document_id) for document_id in document_ids],
                    "top_k": settings.TOP_K,
                },
            )

            if not chunks:
                answer_text = INSUFFICIENT_CONTEXT_MESSAGE
                yield _sse_event("delta", {"text": answer_text})
                committed = min(embedding_result.total_tokens, reserved_amount)
                commit_usage(
                    db=db,
                    workspace_id=workspace_id,
                    amount=committed,
                    usage_date_utc=usage_date_utc,
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
                yield _sse_event("citations", {"citations": []})
                yield _sse_event("usage", _usage_payload(usage_now))
                yield _sse_event("done", {"ok": True})
                return

            llm_started = time.perf_counter()
            stream_result: LLMResult | None = None
            streamed_parts: list[str] = []
            async for event in stream_answer_question_strict_grounded(
                question=question, chunks=chunks
            ):
                if await request.is_disconnected():
                    raise ConnectionError("Client disconnected")
                if event.type == "delta":
                    if event.text:
                        streamed_parts.append(event.text)
                        yield _sse_event("delta", {"text": event.text})
                    continue
                stream_result = event.result

            llm_latency_ms = int((time.perf_counter() - llm_started) * 1000)
            if stream_result is None:
                stream_result = LLMResult(
                    answer="".join(streamed_parts).strip(),
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                )
            answer_text = stream_result.answer or INSUFFICIENT_CONTEXT_MESSAGE
            llm_input_tokens = stream_result.input_tokens
            llm_output_tokens = stream_result.output_tokens
            if not streamed_parts and answer_text == INSUFFICIENT_CONTEXT_MESSAGE:
                yield _sse_event("delta", {"text": answer_text})

            actual_total = embedding_result.total_tokens + stream_result.total_tokens
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

            citations_payload = {
                "citations": [
                    {
                        "document_id": str(chunk.document_id),
                        "page_number": chunk.page_number,
                        "chunk_id": str(chunk.chunk_id),
                        "score": chunk.score,
                        "snippet": chunk.snippet,
                    }
                    for chunk in chunks
                ]
            }
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
            yield _sse_event("citations", citations_payload)
            yield _sse_event("usage", _usage_payload(usage_now))
            yield _sse_event("done", {"ok": True})
        except BudgetExceededError as exc:
            if reserved_amount > 0:
                try:
                    release_tokens(
                        db=db,
                        workspace_id=workspace_id,
                        amount=reserved_amount,
                        usage_date_utc=usage_date_utc,
                    )
                except Exception:  # noqa: BLE001
                    db.rollback()
            yield _sse_event(
                "error",
                _error_payload(
                    f"Daily token limit reached for this workspace. Used={exc.used}, reserved={exc.reserved}, limit={exc.limit}, resets_at={utc_next_reset_at(usage_date_utc).isoformat()}",
                    "BUDGET_EXCEEDED",
                ),
            )
        except ConnectionError:
            if reserved_amount > 0:
                try:
                    release_tokens(
                        db=db,
                        workspace_id=workspace_id,
                        amount=reserved_amount,
                        usage_date_utc=usage_date_utc,
                    )
                except Exception:  # noqa: BLE001
                    db.rollback()
        except HTTPException as exc:
            if reserved_amount > 0:
                try:
                    release_tokens(
                        db=db,
                        workspace_id=workspace_id,
                        amount=reserved_amount,
                        usage_date_utc=usage_date_utc,
                    )
                except Exception:  # noqa: BLE001
                    db.rollback()
            message = str(exc.detail) if isinstance(exc.detail, str) else "Query failed"
            yield _sse_event("error", _error_payload(message, f"HTTP_{exc.status_code}"))
        except Exception as exc:  # noqa: BLE001
            if reserved_amount > 0:
                try:
                    release_tokens(
                        db=db,
                        workspace_id=workspace_id,
                        amount=reserved_amount,
                        usage_date_utc=usage_date_utc,
                    )
                except Exception:  # noqa: BLE001
                    db.rollback()
            total_latency_ms = int((time.perf_counter() - request_started) * 1000)
            try:
                _log_query(
                    db=db,
                    workspace_id=workspace_id,
                    user_id=user.user_id,
                    document_ids=payload.selected_document_ids,
                    question=payload.question.strip(),
                    retrieved_chunks=chunks,
                    answer_text=answer_text,
                    error_message=str(exc),
                    retrieval_latency_ms=retrieval_latency_ms,
                    llm_latency_ms=llm_latency_ms,
                    total_latency_ms=total_latency_ms,
                    embedding_tokens_used=embedding_tokens_used,
                    llm_input_tokens=llm_input_tokens,
                    llm_output_tokens=llm_output_tokens,
                    total_tokens_used=0,
                )
            except Exception:  # noqa: BLE001
                db.rollback()
            yield _sse_event("error", _error_payload(f"Query failed: {exc}", "QUERY_FAILED"))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
