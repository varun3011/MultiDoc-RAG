import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class QueryRequest(BaseModel):
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = None
    question: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_selected_documents(self) -> "QueryRequest":
        if self.document_id is None and not self.document_ids:
            raise ValueError("document_id or document_ids is required")
        return self

    @property
    def selected_document_ids(self) -> list[uuid.UUID]:
        selected: list[uuid.UUID] = []
        if self.document_ids:
            selected.extend(self.document_ids)
        if self.document_id is not None and self.document_id not in selected:
            selected.insert(0, self.document_id)
        return selected


class QueryCitation(BaseModel):
    document_id: uuid.UUID
    page_number: int
    chunk_id: uuid.UUID
    score: float
    snippet: str


class QueryUsage(BaseModel):
    limit: int
    used: int
    reserved: int
    remaining: int
    resets_at: datetime


class QueryResponse(BaseModel):
    answer: str
    citations: list[QueryCitation]
    usage: QueryUsage
