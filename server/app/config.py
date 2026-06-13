from datetime import UTC, date, datetime, time, timedelta

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/enterprise_rag"
    DB_POOL_SIZE: int = 3
    DB_MAX_OVERFLOW: int = 2
    DB_POOL_TIMEOUT_SECONDS: int = 30
    REDIS_URL: str = "redis://localhost:6379/0"

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "documents"
    UPLOAD_URL_EXPIRES_SECONDS: int = 600

    DAILY_TOKEN_LIMIT: int = 100000
    RESERVATION_TTL_SECONDS: int = 600
    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_SECONDS: int = 30
    LLM_MAX_OUTPUT_TOKENS: int = 2000
    TOP_K: int = 5
    MAX_QUESTION_CHARS: int = 500
    MAX_QUERY_DOCUMENTS: int = 10
    MAX_QUERY_TOTAL_PAGES: int = 100
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536
    OPENAI_EMBEDDING_TIMEOUT_SECONDS: int = 300
    LOG_EACH_QUERY: bool = False
    MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024
    MAX_PDF_PAGE_COUNT: int = 10
    MIN_EXTRACTED_TEXT_CHARS: int = 1
    MAX_DOCUMENTS_PER_WORKSPACE: int = 100
    MAX_BULK_UPLOAD_FILES: int = 50
    EMBEDDING_BATCH_SIZE: int = 32
    INGEST_EXTRACT_JOB_TIMEOUT_SECONDS: int = 900
    INGEST_INDEX_JOB_TIMEOUT_SECONDS: int = 1800
    INGEST_STALE_UPLOADED_SECONDS: int = 3600
    INGEST_STALE_EXTRACTING_SECONDS: int = 1800
    INGEST_STALE_INDEXING_SECONDS: int = 3600
    ALLOWED_CONTENT_TYPES: list[str] = Field(default_factory=lambda: ["application/pdf"])

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def supabase_service_key(self) -> str:
        return self.SUPABASE_SERVICE_ROLE_KEY or self.SUPABASE_KEY


settings = Settings()


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_today() -> date:
    return utc_now().date()


def utc_next_reset_at(from_dt: datetime | None = None) -> datetime:
    now_utc = from_dt.astimezone(UTC) if from_dt else utc_now()
    next_day = (now_utc + timedelta(days=1)).date()
    return datetime.combine(next_day, time.min, tzinfo=UTC)
