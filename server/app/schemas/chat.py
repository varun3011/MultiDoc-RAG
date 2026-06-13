from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    ts: datetime
    citations: list[dict[str, object]] | None = None


class ChatSessionCreateRequest(BaseModel):
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = None
    title: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatSessionUpdateRequest(BaseModel):
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = None
    title: str | None = None
    messages: list[ChatMessage] | None = None
    ended: bool = False


class ChatSessionMetadata(BaseModel):
    id: uuid.UUID
    title: str
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None


class ChatSessionListItem(BaseModel):
    id: uuid.UUID
    title: str
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    updated_at: datetime
    ended_at: datetime | None = None


class ChatSessionListResponse(BaseModel):
    items: list[ChatSessionListItem]
    total: int


class ChatSessionDetailResponse(BaseModel):
    id: uuid.UUID
    title: str
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    messages: list[ChatMessage]
    started_at: datetime
    ended_at: datetime | None = None
