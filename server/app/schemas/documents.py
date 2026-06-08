import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class UploadPrepareRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    file_size_bytes: int = Field(gt=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)


class UploadPrepareResponse(BaseModel):
    document_id: uuid.UUID
    bucket: str
    storage_path: str
    upload_url: str
    expires_in: int


class UploadPrepareBatchFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    file_size_bytes: int = Field(gt=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    client_file_id: str | None = Field(default=None, min_length=1, max_length=120)


class UploadPrepareBatchRequest(BaseModel):
    files: list[UploadPrepareBatchFile]
    name: str | None = Field(default=None, min_length=1, max_length=255)


class UploadPrepareBatchItem(BaseModel):
    index: int
    filename: str
    client_file_id: str | None = None
    status: str
    document_id: uuid.UUID | None = None
    bucket: str | None = None
    storage_path: str | None = None
    upload_url: str | None = None
    expires_in: int | None = None
    error: str | None = None


class UploadPrepareBatchResponse(BaseModel):
    ingestion_run_id: uuid.UUID
    bucket: str
    expires_in: int
    accepted_count: int
    rejected_count: int
    items: list[UploadPrepareBatchItem]


class UploadCompleteRequest(BaseModel):
    document_id: uuid.UUID
    bucket: str = Field(min_length=1, max_length=255)
    storage_path: str = Field(min_length=1, max_length=2048)


class UploadCompleteResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    job_id: str


class UploadCompleteBatchRequest(BaseModel):
    files: list[UploadCompleteRequest]
    ingestion_run_id: uuid.UUID | None = None


class UploadCompleteBatchItem(BaseModel):
    index: int
    document_id: uuid.UUID
    status: str
    job_id: str | None = None
    error: str | None = None


class UploadCompleteBatchResponse(BaseModel):
    ingestion_run_id: uuid.UUID | None = None
    accepted_count: int
    failed_count: int
    items: list[UploadCompleteBatchItem]


class DocumentJobResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    job_id: str


class IngestionTiming(BaseModel):
    upload_completed_at: datetime | None = None
    extract_enqueued_at: datetime | None = None
    extract_started_at: datetime | None = None
    extract_finished_at: datetime | None = None
    index_enqueued_at: datetime | None = None
    index_started_at: datetime | None = None
    index_finished_at: datetime | None = None


class DocumentListItem(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    file_size_bytes: int
    page_count: int | None = None
    ingestion_run_id: uuid.UUID | None = None
    status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    timing: IngestionTiming | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    limit: int
    offset: int
    total: int


class DocumentProgress(BaseModel):
    pages_total: int
    pages_extracted_count: int
    chunks_count: int
    embeddings_count: int


class DocumentDetailResponse(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    file_size_bytes: int
    ingestion_run_id: uuid.UUID | None = None
    status: str
    error_message: str | None = None
    bucket: str
    storage_path: str
    created_at: datetime
    updated_at: datetime
    progress: DocumentProgress
    timing: IngestionTiming | None = None


class IngestionRunStatusCounts(BaseModel):
    pending_upload: int = 0
    uploading: int = 0
    uploaded: int = 0
    extracting: int = 0
    indexing: int = 0
    ready: int = 0
    indexed: int = 0
    failed: int = 0
    total: int = 0


class IngestionRunResponse(BaseModel):
    id: uuid.UUID
    name: str | None = None
    status: str
    total_documents: int
    accepted_documents: int
    rejected_documents: int
    document_statuses: IngestionRunStatusCounts
    created_at: datetime
    updated_at: datetime


class IngestionQueueStatusItem(BaseModel):
    name: str
    queued_count: int
    started_count: int
    deferred_count: int
    scheduled_count: int
    failed_count: int


class IngestionQueueStatusResponse(BaseModel):
    queues: list[IngestionQueueStatusItem]


class IngestionHealthFailureSummary(BaseModel):
    total_failed: int
    expected_rejections: int
    infrastructure_failures: int
    unknown_failures: int


class IngestionHealthResponse(BaseModel):
    generated_at: datetime
    queues: list[IngestionQueueStatusItem]
    active_document_count: int
    active_documents_by_status: dict[str, int]
    stale_active_document_count: int
    oldest_active_document_age_seconds: int | None = None
    active_ingestion_run_count: int
    stale_thresholds_seconds: dict[str, int]
    failures: IngestionHealthFailureSummary


class ReconciledDocumentItem(BaseModel):
    id: uuid.UUID
    ingestion_run_id: uuid.UUID | None = None
    previous_status: str
    status: str
    error_message: str
    updated_at: datetime


class ReconciledIngestionRunItem(BaseModel):
    id: uuid.UUID
    status: str


class IngestionReconciliationResponse(BaseModel):
    failed_document_count: int
    refreshed_run_count: int
    documents: list[ReconciledDocumentItem]
    runs: list[ReconciledIngestionRunItem]
