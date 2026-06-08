export const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type UsageToday = {
  used: number;
  reserved: number;
  limit: number;
  remaining: number;
  resets_at: string;
};

export type WorkspaceMe = {
  id: string;
  name: string;
  owner_id: string;
  created_at: string;
  document_count: number;
  documents_by_status: Record<string, number>;
  usage_today: UsageToday;
};

export type Workspace = {
  id: string;
  name: string;
  owner_id: string;
  created_at: string;
};

export type DocumentStatus =
  | "pending_upload"
  | "uploaded"
  | "queued"
  | "extracting"
  | "indexing"
  | "ready"
  | "indexed"
  | "failed";

export type DocumentRecord = {
  id: string;
  filename: string;
  status: DocumentStatus;
  page_count?: number | null;
  file_size_bytes?: number;
  ingestion_run_id?: string | null;
  created_at?: string;
  updated_at?: string;
  error_message?: string | null;
  timing?: IngestionTiming | null;
};

export type IngestionTiming = {
  upload_completed_at?: string | null;
  extract_enqueued_at?: string | null;
  extract_started_at?: string | null;
  extract_finished_at?: string | null;
  index_enqueued_at?: string | null;
  index_started_at?: string | null;
  index_finished_at?: string | null;
};

export type UploadPrepareResponse = {
  document_id: string;
  bucket: string;
  storage_path: string;
  upload_url: string;
  expires_in: number;
};

export type UploadCompleteResponse = {
  document_id: string;
  status: string;
  job_id?: string;
};

export type UploadPrepareBatchFile = {
  filename: string;
  content_type: string;
  file_size_bytes: number;
  idempotency_key?: string;
  client_file_id?: string;
};

export type UploadPrepareBatchItem = {
  index: number;
  filename: string;
  client_file_id?: string | null;
  status: string;
  document_id?: string | null;
  bucket?: string | null;
  storage_path?: string | null;
  upload_url?: string | null;
  expires_in?: number | null;
  error?: string | null;
};

export type UploadPrepareBatchResponse = {
  ingestion_run_id: string;
  bucket: string;
  expires_in: number;
  accepted_count: number;
  rejected_count: number;
  items: UploadPrepareBatchItem[];
};

export type UploadCompleteBatchFile = {
  document_id: string;
  bucket: string;
  storage_path: string;
};

export type UploadCompleteBatchItem = {
  index: number;
  document_id: string;
  status: string;
  job_id?: string | null;
  error?: string | null;
};

export type UploadCompleteBatchResponse = {
  ingestion_run_id?: string | null;
  accepted_count: number;
  failed_count: number;
  items: UploadCompleteBatchItem[];
};

export type DocumentJobResponse = {
  document_id: string;
  status: string;
  job_id: string;
};

export type IngestionRunStatusCounts = {
  pending_upload: number;
  uploading: number;
  queued: number;
  uploaded: number;
  extracting: number;
  indexing: number;
  ready: number;
  indexed: number;
  failed: number;
  total: number;
};

export type IngestionRunResponse = {
  id: string;
  name?: string | null;
  status: string;
  total_documents: number;
  accepted_documents: number;
  rejected_documents: number;
  document_statuses: IngestionRunStatusCounts;
  created_at: string;
  updated_at: string;
};

export type IngestionQueueStatusItem = {
  name: string;
  queued_count: number;
  started_count: number;
  deferred_count: number;
  scheduled_count: number;
  failed_count: number;
};

export type IngestionQueueStatusResponse = {
  queues: IngestionQueueStatusItem[];
};

export type QueryCitation = {
  document_id: string;
  page_number: number;
  chunk_id: string;
  score: number;
  snippet: string;
};

export type QueryResponse = {
  answer: string;
  citations: QueryCitation[];
  usage: UsageToday;
};

export type QueryStreamMeta = {
  request_id: string;
  document_id: string;
  document_ids?: string[];
  top_k: number;
};

export type QueryStreamEventHandlers = {
  onMeta?: (meta: QueryStreamMeta) => void;
  onDelta?: (delta: string) => void;
  onCitations?: (citations: QueryCitation[]) => void;
  onUsage?: (usage: UsageToday) => void;
  onDone?: () => void;
  onError?: (message: string, code: string) => void;
};

export type CitationSource = {
  chunk_id: string;
  document_id: string;
  page_number: number;
  chunk_text: string;
  page_text: string | null;
  highlights: string[];
};

export type DocumentPageSource = {
  document_id: string;
  page_number: number;
  text: string;
};

export type QueryHistoryCitation = {
  page_number: number;
  chunk_id: string;
};

export type QueryHistoryItem = {
  id: string;
  document_id: string | null;
  question: string;
  created_at: string;
  answer_preview: string;
  citations?: QueryHistoryCitation[] | null;
};

export type QueryHistoryListResponse = {
  items: QueryHistoryItem[];
  limit: number;
  offset: number;
  total: number;
};

export type QueryHistoryDetail = {
  id: string;
  workspace_id: string;
  user_id: string;
  question: string;
  document_ids: string[];
  retrieved_chunk_ids: string[];
  chunk_scores: number[];
  answer: string | null;
  error_message: string | null;
  retrieval_latency_ms: number | null;
  llm_latency_ms: number | null;
  total_latency_ms: number;
  embedding_tokens_used: number;
  llm_input_tokens: number | null;
  llm_output_tokens: number | null;
  total_tokens_used: number;
  citations: QueryHistoryCitation[];
  created_at: string;
};

export type ChatSessionMessage = {
  role: "user" | "assistant";
  content: string;
  ts: string;
  citations?: Array<Record<string, unknown>>;
};

export type ChatSessionMetadata = {
  id: string;
  title: string;
  document_id: string | null;
  created_at: string;
  updated_at: string;
  ended_at: string | null;
};

export type ChatSessionListItem = {
  id: string;
  title: string;
  document_id: string | null;
  updated_at: string;
  ended_at: string | null;
};

export type ChatSessionListResponse = {
  items: ChatSessionListItem[];
  total: number;
};

export type ChatSessionDetail = {
  id: string;
  title: string;
  document_id: string | null;
  messages: ChatSessionMessage[];
  started_at: string;
  ended_at: string | null;
};

export type ObservabilityQuerySummary = {
  total_queries: number;
  queries_last_24h: number;
  error_count_last_24h: number;
  error_rate_last_24h: number;
  avg_latency_ms_last_24h: number;
  p95_latency_ms_last_24h: number;
};

export type ObservabilityQueryVolumePoint = {
  date: string;
  count: number;
  errors: number;
};

export type ObservabilityDocumentSummary = {
  total: number;
  ready: number;
  processing: number;
  failed: number;
};

export type ObservabilityTopDocument = {
  document_id: string;
  filename: string;
  query_count: number;
  error_count: number;
  last_queried_at: string | null;
};

export type ObservabilityRecentError = {
  query_id: string;
  created_at: string;
  question: string;
  error_message: string;
  document_id: string | null;
};

export type ObservabilityResponse = {
  generated_at: string;
  window_days: number;
  usage_today: UsageToday;
  query_summary: ObservabilityQuerySummary;
  query_volume: ObservabilityQueryVolumePoint[];
  documents: ObservabilityDocumentSummary;
  top_documents: ObservabilityTopDocument[];
  recent_errors: ObservabilityRecentError[];
};

export type AuthMeResponse = {
  user_id: string;
  email: string | null;
  role: string | null;
};

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

let unauthorizedHandler: (() => void | Promise<void>) | null = null;

export function setUnauthorizedHandler(handler: (() => void | Promise<void>) | null): void {
  unauthorizedHandler = handler;
}

function parsePayloadMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (typeof detail === "object" && detail !== null && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
  }

  if (typeof payload === "object" && payload !== null && "error" in payload) {
    const error = (payload as { error: unknown }).error;
    if (typeof error === "object" && error !== null && "message" in error) {
      return String((error as { message: unknown }).message);
    }
  }

  return fallback;
}

async function apiRequest<T>(path: string, token: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options?.headers ?? {}),
    },
  });

  const text = await response.text();
  let payload: unknown = null;

  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && unauthorizedHandler) {
      await unauthorizedHandler();
    }

    const message = parsePayloadMessage(payload, `Request failed with status ${response.status}`);
    throw new ApiError(message, response.status, payload);
  }

  return payload as T;
}

function ensureUsage(value: unknown): UsageToday {
  const raw = (value ?? {}) as Record<string, unknown>;
  const used = Number(raw.used ?? raw.tokens_used ?? 0);
  const reserved = Number(raw.reserved ?? raw.tokens_reserved ?? 0);
  const limit = Number(raw.limit ?? 100000);
  const remaining = Number(raw.remaining ?? Math.max(limit - used - reserved, 0));
  const resets_at = String(raw.resets_at ?? "");

  return {
    used,
    reserved,
    limit,
    remaining,
    resets_at,
  };
}

function normalizeDocumentStatus(input: unknown): DocumentStatus {
  const status = String(input ?? "uploaded").toLowerCase();

  if (
    status === "pending_upload" ||
    status === "uploaded" ||
    status === "queued" ||
    status === "extracting" ||
    status === "indexing" ||
    status === "ready" ||
    status === "indexed" ||
    status === "failed"
  ) {
    return status;
  }

  return "uploaded";
}

function normalizeDocument(rawDoc: unknown): DocumentRecord | null {
  if (typeof rawDoc !== "object" || rawDoc === null) {
    return null;
  }

  const raw = rawDoc as Record<string, unknown>;
  if (!raw.id || !raw.filename) {
    return null;
  }

  return {
    id: String(raw.id),
    filename: String(raw.filename),
    status: normalizeDocumentStatus(raw.status),
    page_count: raw.page_count == null ? null : Number(raw.page_count),
    file_size_bytes: raw.file_size_bytes == null ? undefined : Number(raw.file_size_bytes),
    ingestion_run_id: raw.ingestion_run_id == null ? null : String(raw.ingestion_run_id),
    created_at: raw.created_at ? String(raw.created_at) : undefined,
    updated_at: raw.updated_at ? String(raw.updated_at) : undefined,
    error_message: raw.error_message ? String(raw.error_message) : null,
    timing: normalizeIngestionTiming(raw.timing),
  };
}

function normalizeIngestionTiming(value: unknown): IngestionTiming | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const raw = value as Record<string, unknown>;
  return {
    upload_completed_at: raw.upload_completed_at == null ? null : String(raw.upload_completed_at),
    extract_enqueued_at: raw.extract_enqueued_at == null ? null : String(raw.extract_enqueued_at),
    extract_started_at: raw.extract_started_at == null ? null : String(raw.extract_started_at),
    extract_finished_at: raw.extract_finished_at == null ? null : String(raw.extract_finished_at),
    index_enqueued_at: raw.index_enqueued_at == null ? null : String(raw.index_enqueued_at),
    index_started_at: raw.index_started_at == null ? null : String(raw.index_started_at),
    index_finished_at: raw.index_finished_at == null ? null : String(raw.index_finished_at),
  };
}

export function normalizeDocuments(payload: unknown): DocumentRecord[] {
  const source = Array.isArray(payload)
    ? payload
    : typeof payload === "object" && payload !== null
      ? ((payload as { documents?: unknown; items?: unknown }).documents ??
          (payload as { items?: unknown }).items ??
          [])
      : [];

  if (!Array.isArray(source)) {
    return [];
  }

  return source
    .map((doc) => normalizeDocument(doc))
    .filter((doc): doc is DocumentRecord => Boolean(doc));
}

export async function apiGetWorkspaceMe(token: string): Promise<WorkspaceMe> {
  const payload = await apiRequest<Record<string, unknown>>("/workspaces/me", token, { method: "GET" });

  return {
    id: String(payload.id),
    name: String(payload.name),
    owner_id: String(payload.owner_id),
    created_at: String(payload.created_at),
    document_count: Number(payload.document_count ?? 0),
    documents_by_status: (payload.documents_by_status ?? {}) as Record<string, number>,
    usage_today: ensureUsage(payload.usage_today),
  };
}

export function apiCreateWorkspace(token: string, name: string): Promise<Workspace> {
  return apiRequest<Workspace>("/workspaces", token, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function apiGetAuthMe(token: string): Promise<AuthMeResponse> {
  return apiRequest<AuthMeResponse>("/auth/me", token, { method: "GET" });
}

export function apiAuthMe(token: string): Promise<AuthMeResponse> {
  return apiGetAuthMe(token);
}

export async function apiGetDocuments(token: string, params?: { limit?: number; offset?: number; status?: string }): Promise<DocumentRecord[]> {
  const search = new URLSearchParams();
  if (typeof params?.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (typeof params?.offset === "number") {
    search.set("offset", String(params.offset));
  }
  if (params?.status) {
    search.set("status", params.status);
  }
  const suffix = search.toString() ? `?${search.toString()}` : "";
  const payload = await apiRequest<unknown>(`/documents${suffix}`, token, { method: "GET" });
  return normalizeDocuments(payload);
}

export async function apiGetDocument(token: string, documentId: string): Promise<DocumentRecord> {
  const payload = await apiRequest<unknown>(`/documents/${documentId}`, token, { method: "GET" });
  const normalized = normalizeDocument(payload);

  if (!normalized) {
    throw new ApiError("Document response shape was invalid", 500, payload);
  }

  return normalized;
}

export async function apiDeleteDocument(token: string, documentId: string): Promise<void> {
  await apiRequest<null>(`/documents/${documentId}`, token, { method: "DELETE" });
}

export function apiRetryDocument(token: string, documentId: string): Promise<DocumentJobResponse> {
  return apiRequest<DocumentJobResponse>(`/documents/${documentId}/retry`, token, { method: "POST" });
}

export function apiReindexDocument(token: string, documentId: string): Promise<DocumentJobResponse> {
  return apiRequest<DocumentJobResponse>(`/documents/${documentId}/reindex`, token, { method: "POST" });
}

export async function apiGetUsageToday(token: string): Promise<UsageToday> {
  const payload = await apiRequest<Record<string, unknown>>("/usage/today", token, { method: "GET" });
  return ensureUsage(payload);
}

export function apiUploadPrepare(
  token: string,
  payload: { filename: string; content_type: string; file_size_bytes: number; idempotency_key?: string },
): Promise<UploadPrepareResponse> {
  return apiRequest<UploadPrepareResponse>("/documents/upload-prepare", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function apiUploadToSignedUrl(uploadUrl: string, file: File): Promise<void> {
  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers: {
      "Content-Type": file.type || "application/pdf",
    },
    body: file,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Storage upload failed (${response.status}): ${errorText || "unknown error"}`);
  }
}

export function apiUploadComplete(
  token: string,
  payload: { document_id: string; bucket: string; storage_path: string },
): Promise<UploadCompleteResponse> {
  return apiRequest<UploadCompleteResponse>("/documents/upload-complete", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUploadPrepareBatch(
  token: string,
  payload: { files: UploadPrepareBatchFile[]; name?: string },
): Promise<UploadPrepareBatchResponse> {
  return apiRequest<UploadPrepareBatchResponse>("/documents/upload-prepare-batch", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUploadCompleteBatch(
  token: string,
  payload: { files: UploadCompleteBatchFile[]; ingestion_run_id?: string | null },
): Promise<UploadCompleteBatchResponse> {
  return apiRequest<UploadCompleteBatchResponse>("/documents/upload-complete-batch", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiGetIngestionRun(token: string, runId: string): Promise<IngestionRunResponse> {
  return apiRequest<IngestionRunResponse>(`/documents/ingestion-runs/${runId}`, token, { method: "GET" });
}

export function apiGetIngestionQueues(token: string): Promise<IngestionQueueStatusResponse> {
  return apiRequest<IngestionQueueStatusResponse>("/documents/ingestion-queues", token, { method: "GET" });
}

export async function apiUploadDocument(token: string, file: File): Promise<UploadCompleteResponse> {
  const prepare = await apiUploadPrepare(token, {
    filename: file.name,
    content_type: file.type || "application/pdf",
    file_size_bytes: file.size,
  });

  await apiUploadToSignedUrl(prepare.upload_url, file);

  return apiUploadComplete(token, {
    document_id: prepare.document_id,
    bucket: prepare.bucket,
    storage_path: prepare.storage_path,
  });
}

export async function apiQueryDocument(
  token: string,
  payload: { document_id?: string; document_ids?: string[]; question: string },
): Promise<QueryResponse> {
  const response = await apiRequest<Record<string, unknown>>("/query", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });

  const rawCitations = Array.isArray(response.citations) ? response.citations : [];
  const citations: QueryCitation[] = rawCitations
    .map((raw) => {
      if (typeof raw !== "object" || raw === null) {
        return null;
      }
      const value = raw as Record<string, unknown>;
      if (!value.document_id || !value.chunk_id || value.page_number == null || value.score == null) {
        return null;
      }
      return {
        document_id: String(value.document_id),
        page_number: Number(value.page_number),
        chunk_id: String(value.chunk_id),
        score: Number(value.score),
        snippet: String(value.snippet ?? ""),
      };
    })
    .filter((item): item is QueryCitation => Boolean(item));

  return {
    answer: String(response.answer ?? ""),
    citations,
    usage: ensureUsage(response.usage),
  };
}

function parseSseEvent(block: string): { event: string; data: unknown } | null {
  const lines = block
    .split("\n")
    .map((line) => line.replace(/\r$/, ""))
    .filter((line) => line.length > 0);

  if (lines.length === 0) {
    return null;
  }

  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  const rawData = dataLines.join("\n");
  if (!rawData) {
    return { event, data: null };
  }
  try {
    return { event, data: JSON.parse(rawData) as unknown };
  } catch {
    return { event, data: rawData };
  }
}

function normalizeCitations(value: unknown): QueryCitation[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((raw) => {
      if (typeof raw !== "object" || raw === null) {
        return null;
      }
      const item = raw as Record<string, unknown>;
      if (!item.document_id || !item.chunk_id || item.page_number == null || item.score == null) {
        return null;
      }
      return {
        document_id: String(item.document_id),
        page_number: Number(item.page_number),
        chunk_id: String(item.chunk_id),
        score: Number(item.score),
        snippet: String(item.snippet ?? ""),
      };
    })
    .filter((item): item is QueryCitation => Boolean(item));
}

export async function apiQueryStream(
  token: string,
  payload: { document_id?: string; document_ids?: string[]; question: string },
  handlers: QueryStreamEventHandlers,
  abortSignal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/query/stream`, {
    method: "POST",
    signal: abortSignal,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    if (response.status === 401 && unauthorizedHandler) {
      await unauthorizedHandler();
    }
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text) as unknown;
      } catch {
        body = text;
      }
    }
    throw new ApiError(parsePayloadMessage(body, `Request failed with status ${response.status}`), response.status, body);
  }

  if (!response.body) {
    throw new ApiError("Streaming response body was empty.", 500, null);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    buffer = buffer.replace(/\r\n/g, "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSseEvent(block);
      if (!parsed) {
        boundary = buffer.indexOf("\n\n");
        continue;
      }

      const payloadData = parsed.data as Record<string, unknown> | null;
      if (parsed.event === "meta" && payloadData) {
        handlers.onMeta?.({
          request_id: String(payloadData.request_id ?? ""),
          document_id: String(payloadData.document_id ?? ""),
          document_ids: Array.isArray(payloadData.document_ids)
            ? payloadData.document_ids.map((item) => String(item))
            : undefined,
          top_k: Number(payloadData.top_k ?? 0),
        });
      } else if (parsed.event === "delta" && payloadData) {
        handlers.onDelta?.(String(payloadData.text ?? ""));
      } else if (parsed.event === "citations" && payloadData) {
        handlers.onCitations?.(normalizeCitations(payloadData.citations));
      } else if (parsed.event === "usage" && payloadData) {
        handlers.onUsage?.(ensureUsage(payloadData.usage));
      } else if (parsed.event === "done") {
        handlers.onDone?.();
        return;
      } else if (parsed.event === "error") {
        const message = payloadData ? String(payloadData.message ?? "Streaming query failed.") : "Streaming query failed.";
        const code = payloadData ? String(payloadData.code ?? "STREAM_ERROR") : "STREAM_ERROR";
        handlers.onError?.(message, code);
        throw new ApiError(message, 400, payloadData);
      }

      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      break;
    }
  }
}

export async function apiListQueries(
  token: string,
  params?: { document_id?: string; limit?: number; offset?: number },
): Promise<QueryHistoryListResponse> {
  const search = new URLSearchParams();
  if (params?.document_id) {
    search.set("document_id", params.document_id);
  }
  if (typeof params?.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (typeof params?.offset === "number") {
    search.set("offset", String(params.offset));
  }
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return apiRequest<QueryHistoryListResponse>(`/queries${suffix}`, token, { method: "GET" });
}

export function apiGetQuery(token: string, queryId: string): Promise<QueryHistoryDetail> {
  return apiRequest<QueryHistoryDetail>(`/queries/${queryId}`, token, { method: "GET" });
}

export function apiGetObservability(token: string): Promise<ObservabilityResponse> {
  return apiRequest<ObservabilityResponse>("/usage/observability", token, { method: "GET" });
}

export function apiCreateChatSession(
  token: string,
  payload: { document_id?: string | null; title?: string; messages: ChatSessionMessage[] },
): Promise<ChatSessionMetadata> {
  return apiRequest<ChatSessionMetadata>("/chats/sessions", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateChatSession(
  token: string,
  sessionId: string,
  payload: { title?: string; messages?: ChatSessionMessage[]; ended?: boolean },
): Promise<ChatSessionMetadata> {
  return apiRequest<ChatSessionMetadata>(`/chats/sessions/${sessionId}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function apiListChatSessions(
  token: string,
  params?: { document_id?: string; limit?: number; offset?: number },
): Promise<ChatSessionListResponse> {
  const search = new URLSearchParams();
  if (params?.document_id) {
    search.set("document_id", params.document_id);
  }
  if (typeof params?.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (typeof params?.offset === "number") {
    search.set("offset", String(params.offset));
  }
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return apiRequest<ChatSessionListResponse>(`/chats/sessions${suffix}`, token, { method: "GET" });
}

export function apiGetChatSession(token: string, sessionId: string): Promise<ChatSessionDetail> {
  return apiRequest<ChatSessionDetail>(`/chats/sessions/${sessionId}`, token, { method: "GET" });
}

export async function apiGetCitation(token: string, chunkId: string, maxChars = 5000): Promise<CitationSource> {
  const boundedMaxChars = Math.max(1, Math.min(20000, Math.trunc(maxChars)));
  const payload = await apiRequest<Record<string, unknown>>(
    `/citations/${chunkId}?max_chars=${boundedMaxChars}`,
    token,
    { method: "GET" },
  );

  const rawHighlights = Array.isArray(payload.highlights) ? payload.highlights : [];
  return {
    chunk_id: String(payload.chunk_id ?? ""),
    document_id: String(payload.document_id ?? ""),
    page_number: Number(payload.page_number ?? 0),
    chunk_text: String(payload.chunk_text ?? ""),
    page_text: payload.page_text == null ? null : String(payload.page_text),
    highlights: rawHighlights.map((item) => String(item)),
  };
}

export async function apiGetPage(
  token: string,
  documentId: string,
  pageNumber: number,
  maxChars = 5000,
): Promise<DocumentPageSource> {
  const boundedMaxChars = Math.max(1, Math.min(20000, Math.trunc(maxChars)));
  const payload = await apiRequest<Record<string, unknown>>(
    `/documents/${documentId}/pages/${pageNumber}?max_chars=${boundedMaxChars}`,
    token,
    { method: "GET" },
  );

  return {
    document_id: String(payload.document_id ?? documentId),
    page_number: Number(payload.page_number ?? pageNumber),
    text: String(payload.text ?? ""),
  };
}
