import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  FileText,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import UploadPanel from "../components/upload/UploadPanel";
import { useAuth } from "../context/AuthContext";
import { useAppShellContext } from "../components/layout/AppShell";
import {
  apiDeleteDocument,
  apiGetIngestionQueues,
  apiGetIngestionRun,
  apiRetryDocument,
  type DocumentRecord,
  type DocumentStatus,
  type IngestionQueueStatusResponse,
  type IngestionRunResponse,
  type IngestionRunStatusCounts,
} from "../lib/api";
import { cn } from "../lib/utils";

const processingStatuses = new Set<DocumentStatus>(["pending_upload", "uploaded", "queued", "extracting", "indexing"]);
const readyStatuses = new Set<DocumentStatus>(["ready", "indexed"]);
const terminalFailurePrefixes = [
  "Validation failure:",
  "Upload/storage failure:",
  "Page-limit failure:",
  "Unsupported-content failure:",
];

type StatusFilter = "all" | "processing" | "failed" | "ready";
type SortMode = "newest" | "oldest" | "status" | "updated";

function isProcessing(status: DocumentStatus): boolean {
  return processingStatuses.has(status);
}

function isReady(status: DocumentStatus): boolean {
  return readyStatuses.has(status);
}

function isRetryableFailure(errorMessage?: string | null): boolean {
  if (!errorMessage) {
    return true;
  }
  return !terminalFailurePrefixes.some((prefix) => errorMessage.startsWith(prefix));
}

function statusLabel(status: DocumentStatus): string {
  if (status === "indexed" || status === "ready") return "Ready";
  if (status === "pending_upload") return "Waiting to upload";
  if (status === "uploaded" || status === "queued") return "Queued";
  if (status === "extracting") return "Extracting text";
  if (status === "indexing") return "Indexing";
  if (status === "failed") return "Failed";
  return String(status);
}

function statusHelp(status: DocumentStatus): string {
  if (status === "pending_upload") return "The document has a placeholder but no uploaded object yet.";
  if (status === "uploaded" || status === "queued") return "The file is uploaded and waiting for a worker.";
  if (status === "extracting") return "Text is being extracted from the PDF.";
  if (status === "indexing") return "Chunks and embeddings are being created.";
  if (status === "ready" || status === "indexed") return "The document is available for chat.";
  if (status === "failed") return "Ingestion stopped for this document.";
  return "Document status";
}

function statusBadge(status: DocumentStatus): string {
  if (isReady(status)) {
    return "border-green-200 bg-green-50 text-green-700";
  }
  if (status === "failed") {
    return "border-red-200 bg-red-50 text-red-700";
  }
  return "border-amber-200 bg-amber-50 text-amber-700";
}

function statusRank(status: DocumentStatus): number {
  if (isProcessing(status)) return 0;
  if (status === "failed") return 1;
  if (isReady(status)) return 2;
  return 3;
}

function timestampValue(value?: string): number {
  const parsed = value ? new Date(value).getTime() : 0;
  return Number.isFinite(parsed) ? parsed : 0;
}

function compareByFilename(a: DocumentRecord, b: DocumentRecord): number {
  const filenameCompare = a.filename.localeCompare(b.filename, undefined, {
    numeric: true,
    sensitivity: "base",
  });
  if (filenameCompare !== 0) {
    return filenameCompare;
  }
  return a.id.localeCompare(b.id);
}

function formatDate(value?: string): string {
  return value ? new Date(value).toLocaleString() : "--";
}

function durationMs(start?: string | null, end?: string | null): number | null {
  if (!start || !end) {
    return null;
  }
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
    return null;
  }
  return endMs - startMs;
}

function formatDuration(ms: number | null): string | null {
  if (ms === null) {
    return null;
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return `${minutes}m ${remaining}s`;
}

function ingestionDurations(document: DocumentRecord) {
  const timing = document.timing;
  if (!timing) {
    return [];
  }
  return [
    {
      label: "Queue",
      value: formatDuration(durationMs(timing.extract_enqueued_at, timing.extract_started_at)),
    },
    {
      label: "Extract",
      value: formatDuration(durationMs(timing.extract_started_at, timing.extract_finished_at)),
    },
    {
      label: "Index wait",
      value: formatDuration(durationMs(timing.index_enqueued_at, timing.index_started_at)),
    },
    {
      label: "Index",
      value: formatDuration(durationMs(timing.index_started_at, timing.index_finished_at)),
    },
    {
      label: "Total",
      value: formatDuration(durationMs(timing.upload_completed_at, timing.index_finished_at)),
    },
  ].filter((item): item is { label: string; value: string } => Boolean(item.value));
}

function countDocuments(documents: DocumentRecord[]): IngestionRunStatusCounts {
  const counts: IngestionRunStatusCounts = {
    pending_upload: 0,
    uploading: 0,
    queued: 0,
    uploaded: 0,
    extracting: 0,
    indexing: 0,
    ready: 0,
    indexed: 0,
    failed: 0,
    total: 0,
  };

  documents.forEach((doc) => {
    counts[doc.status] += 1;
    counts.total += 1;
  });

  return counts;
}

function normalizeCounts(counts: Partial<IngestionRunStatusCounts>): IngestionRunStatusCounts {
  return {
    pending_upload: counts.pending_upload ?? 0,
    uploading: counts.uploading ?? 0,
    queued: counts.queued ?? 0,
    uploaded: counts.uploaded ?? 0,
    extracting: counts.extracting ?? 0,
    indexing: counts.indexing ?? 0,
    ready: counts.ready ?? 0,
    indexed: counts.indexed ?? 0,
    failed: counts.failed ?? 0,
    total: counts.total ?? 0,
  };
}

function completedCount(counts: IngestionRunStatusCounts): number {
  return counts.ready + counts.indexed + counts.failed;
}

function queueTotals(queueStatus: IngestionQueueStatusResponse | null) {
  return (queueStatus?.queues ?? []).reduce(
    (acc, queue) => ({
      queued: acc.queued + queue.queued_count,
      running: acc.running + queue.started_count,
      failed: acc.failed + queue.failed_count,
    }),
    { queued: 0, running: 0, failed: 0 },
  );
}

export default function UploadPage() {
  const { accessToken } = useAuth();
  const {
    documents,
    loading,
    refreshDocuments,
    refreshWorkspace,
    activeDocument,
    setActiveDocumentId,
  } = useAppShellContext();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteAllProgress, setDeleteAllProgress] = useState<{ done: number; total: number } | null>(null);
  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set());
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<IngestionRunResponse | null>(null);
  const [queueStatus, setQueueStatus] = useState<IngestionQueueStatusResponse | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("newest");

  const hasProcessing = useMemo(() => documents.some((doc) => isProcessing(doc.status)), [documents]);

  useEffect(() => {
    if (activeRunId) {
      return;
    }

    const latestRunDocument = documents.find((doc) => doc.ingestion_run_id && isProcessing(doc.status))
      ?? documents.find((doc) => doc.ingestion_run_id);

    if (latestRunDocument?.ingestion_run_id) {
      setActiveRunId(latestRunDocument.ingestion_run_id);
    }
  }, [activeRunId, documents]);

  const refreshIngestionState = useCallback(async () => {
    if (!accessToken) {
      return;
    }

    try {
      const [queues, run] = await Promise.all([
        apiGetIngestionQueues(accessToken),
        activeRunId ? apiGetIngestionRun(accessToken, activeRunId) : Promise.resolve(null),
      ]);
      setQueueStatus(queues);
      setActiveRun(run);
      setStatusError(null);
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : "Unable to refresh ingestion state.");
    }
  }, [accessToken, activeRunId]);

  useEffect(() => {
    if (!accessToken) {
      return;
    }

    void refreshIngestionState();

    const runStillProcessing = activeRun?.status === "processing";
    if (!hasProcessing && !runStillProcessing) {
      return;
    }

    const interval = window.setInterval(() => {
      void refreshDocuments();
      void refreshWorkspace();
      void refreshIngestionState();
    }, 4000);

    return () => {
      window.clearInterval(interval);
    };
  }, [accessToken, activeRun?.status, hasProcessing, refreshDocuments, refreshIngestionState, refreshWorkspace]);

  if (!accessToken) {
    return null;
  }

  const counts = countDocuments(documents);
  const processingCount = counts.pending_upload + counts.uploaded + counts.queued + counts.extracting + counts.indexing;
  const readyCount = counts.ready + counts.indexed;
  const failedCount = counts.failed;

  const visibleDocuments = documents
    .filter((doc) => doc.filename.toLowerCase().includes(query.toLowerCase()))
    .filter((doc) => {
      if (statusFilter === "processing") return isProcessing(doc.status);
      if (statusFilter === "failed") return doc.status === "failed";
      if (statusFilter === "ready") return isReady(doc.status);
      return true;
    })
    .sort((a, b) => {
      let primary = 0;
      if (sortMode === "oldest") {
        primary = timestampValue(a.created_at) - timestampValue(b.created_at);
      } else if (sortMode === "updated") {
        primary = timestampValue(b.updated_at) - timestampValue(a.updated_at);
      } else if (sortMode === "status") {
        primary = statusRank(a.status) - statusRank(b.status);
      } else {
        primary = timestampValue(b.created_at) - timestampValue(a.created_at);
      }
      return primary || compareByFilename(a, b);
    });

  const activeDocuments = visibleDocuments.filter((doc) => isProcessing(doc.status));
  const failedDocuments = visibleDocuments.filter((doc) => doc.status === "failed");
  const readyDocuments = visibleDocuments.filter((doc) => isReady(doc.status));
  const retryableFailedDocuments = documents.filter((doc) => doc.status === "failed" && isRetryableFailure(doc.error_message));

  const handleDelete = async (documentId: string, filename: string) => {
    const confirmed = window.confirm(`Delete document "${filename}"? This cannot be undone.`);
    if (!confirmed) {
      return;
    }

    try {
      setDeletingId(documentId);
      await apiDeleteDocument(accessToken, documentId);
      if (activeDocument?.id === documentId) {
        setActiveDocumentId(null);
      }
      await Promise.all([refreshDocuments(), refreshWorkspace(), refreshIngestionState()]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Delete failed";
      window.alert(message);
    } finally {
      setDeletingId(null);
    }
  };

  const handleDeleteAll = async () => {
    if (documents.length === 0) {
      return;
    }

    const confirmed = window.confirm(
      `Delete all ${documents.length} documents in this workspace? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }

    setDeleteAllProgress({ done: 0, total: documents.length });
    const failures: string[] = [];

    for (const [index, document] of documents.entries()) {
      try {
        await apiDeleteDocument(accessToken, document.id);
        if (activeDocument?.id === document.id) {
          setActiveDocumentId(null);
        }
      } catch (error) {
        failures.push(`${document.filename}: ${error instanceof Error ? error.message : "Delete failed"}`);
      } finally {
        setDeleteAllProgress({ done: index + 1, total: documents.length });
      }
    }

    setDeleteAllProgress(null);
    await Promise.all([refreshDocuments(), refreshWorkspace(), refreshIngestionState()]);

    if (failures.length > 0) {
      window.alert(`Some documents could not be deleted:\n${failures.join("\n")}`);
    }
  };

  const retryDocuments = async (targets: DocumentRecord[]) => {
    const retryableTargets = targets.filter((doc) => isRetryableFailure(doc.error_message));
    if (retryableTargets.length === 0) {
      window.alert("No retryable failed documents are selected.");
      return;
    }

    setRetryingIds((current) => {
      const next = new Set(current);
      retryableTargets.forEach((doc) => next.add(doc.id));
      return next;
    });

    const failures: string[] = [];
    for (const document of retryableTargets) {
      try {
        await apiRetryDocument(accessToken, document.id);
      } catch (error) {
        failures.push(`${document.filename}: ${error instanceof Error ? error.message : "Retry failed"}`);
      }
    }

    setRetryingIds((current) => {
      const next = new Set(current);
      retryableTargets.forEach((doc) => next.delete(doc.id));
      return next;
    });

    await Promise.all([refreshDocuments(), refreshWorkspace(), refreshIngestionState()]);

    if (failures.length > 0) {
      window.alert(`Some retries failed:\n${failures.join("\n")}`);
    }
  };

  return (
    <div className="space-y-5 p-4 md:p-6">
      <UploadPanel
        token={accessToken}
        onAfterUpload={async () => {
          await Promise.all([refreshDocuments(), refreshWorkspace(), refreshIngestionState()]);
        }}
        documents={documents}
        onRunCreated={setActiveRunId}
      />

      <IngestionSummary
        run={activeRun}
        runDocuments={activeRunId ? documents.filter((doc) => doc.ingestion_run_id === activeRunId) : []}
        queueStatus={queueStatus}
        statusError={statusError}
        onRefresh={() => {
          void Promise.all([refreshDocuments(), refreshWorkspace(), refreshIngestionState()]);
        }}
      />

      <section className="rounded-2xl border border-app-border bg-white p-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-app-text">Ingestion workspace</h2>
            <p className="mt-1 text-sm text-app-muted">Monitor processing, recover failures, and confirm which documents are ready.</p>
          </div>

          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <SummaryMetric label="Total" value={documents.length} icon={<FileText size={15} />} />
            <SummaryMetric label="Processing" value={processingCount} icon={<Clock3 size={15} />} tone="warning" />
            <SummaryMetric label="Ready" value={readyCount} icon={<CheckCircle2 size={15} />} tone="success" />
            <SummaryMetric label="Failed" value={failedCount} icon={<AlertTriangle size={15} />} tone="danger" />
          </div>
        </div>

        <div className="mt-5 flex flex-col gap-3 md:flex-row md:items-center">
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-app-border bg-white px-3 py-2">
            <Search size={15} className="text-app-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by filename"
              className="min-w-0 flex-1 border-none bg-transparent p-0 text-sm text-app-text outline-none placeholder:text-app-muted"
            />
          </label>

          <div className="flex flex-wrap gap-2">
            {(["all", "processing", "failed", "ready"] as StatusFilter[]).map((filter) => (
              <button
                key={filter}
                type="button"
                className={cn(
                  "rounded-lg border px-3 py-2 text-xs font-medium capitalize transition",
                  statusFilter === filter
                    ? "border-app-accent bg-app-accentSoft text-app-text"
                    : "border-app-border bg-white text-app-muted hover:border-app-accent",
                )}
                onClick={() => setStatusFilter(filter)}
              >
                {filter}
              </button>
            ))}
          </div>

          <select
            value={sortMode}
            onChange={(event) => setSortMode(event.target.value as SortMode)}
            className="rounded-xl border border-app-border bg-white px-3 py-2 text-sm text-app-text outline-none focus:border-app-accent"
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="updated">Recently updated</option>
            <option value="status">Status order</option>
          </select>

          <button
            type="button"
            className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={() => void handleDeleteAll()}
            disabled={documents.length === 0 || deleteAllProgress !== null || deletingId !== null}
          >
            <Trash2 size={15} />
            {deleteAllProgress ? `Deleting ${deleteAllProgress.done}/${deleteAllProgress.total}` : "Delete all"}
          </button>
        </div>

        <div className="mt-5 space-y-5">
          {(statusFilter === "all" || statusFilter === "processing") ? (
            <DocumentSection
              title="Active processing"
              count={activeDocuments.length}
              emptyText={loading ? "Loading documents..." : "No documents are currently processing."}
            >
              {activeDocuments.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  document={doc}
                  deleting={deletingId === doc.id}
                  retrying={retryingIds.has(doc.id)}
                  onDelete={() => void handleDelete(doc.id, doc.filename)}
                />
              ))}
            </DocumentSection>
          ) : null}

          {(statusFilter === "all" || statusFilter === "failed") ? (
            <DocumentSection
              title="Failed documents"
              count={failedDocuments.length}
              emptyText={loading ? "Loading documents..." : "No failed documents match this view."}
              action={(
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-app-border bg-white px-3 py-1.5 text-xs font-medium text-app-text hover:border-app-accent disabled:cursor-not-allowed disabled:opacity-60"
                  onClick={() => void retryDocuments(retryableFailedDocuments)}
                  disabled={retryableFailedDocuments.length === 0}
                >
                  <RotateCcw size={13} />
                  Retry all retryable
                </button>
              )}
            >
              {failedDocuments.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  document={doc}
                  deleting={deletingId === doc.id}
                  retrying={retryingIds.has(doc.id)}
                  onRetry={isRetryableFailure(doc.error_message) ? () => void retryDocuments([doc]) : undefined}
                  onDelete={() => void handleDelete(doc.id, doc.filename)}
                />
              ))}
            </DocumentSection>
          ) : null}

          {(statusFilter === "all" || statusFilter === "ready") ? (
            <DocumentSection
              title="Ready documents"
              count={readyDocuments.length}
              emptyText={loading ? "Loading documents..." : "No ready documents match this view."}
            >
              {readyDocuments.map((doc) => (
                <DocumentRow
                  key={doc.id}
                  document={doc}
                  deleting={deletingId === doc.id}
                  retrying={retryingIds.has(doc.id)}
                  onDelete={() => void handleDelete(doc.id, doc.filename)}
                />
              ))}
            </DocumentSection>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function IngestionSummary({
  run,
  runDocuments,
  queueStatus,
  statusError,
  onRefresh,
}: {
  run: IngestionRunResponse | null;
  runDocuments: DocumentRecord[];
  queueStatus: IngestionQueueStatusResponse | null;
  statusError: string | null;
  onRefresh: () => void;
}) {
  const counts = normalizeCounts(run?.document_statuses ?? countDocuments(runDocuments));
  const acceptedDocuments = run?.accepted_documents ?? runDocuments.length;
  const rejectedDocuments = run?.rejected_documents ?? 0;
  const doneCount = completedCount(counts);
  const progress = acceptedDocuments > 0 ? Math.round((doneCount / acceptedDocuments) * 100) : 0;
  const queues = queueTotals(queueStatus);
  const hasRun = Boolean(run) || runDocuments.length > 0;

  return (
    <section className="rounded-2xl border border-app-border bg-white p-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-app-text">Current ingestion run</h2>
          <p className="mt-1 text-sm text-app-muted">
            {hasRun
              ? `${doneCount} of ${acceptedDocuments} accepted documents have reached a final state.`
              : "Start a batch upload to track one ingestion run here."}
          </p>
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-lg border border-app-border bg-white px-3 py-2 text-xs font-medium text-app-text hover:border-app-accent"
          onClick={onRefresh}
        >
          <RefreshCw size={13} />
          Refresh status
        </button>
      </div>

      <div className="mt-4 h-2 rounded-full bg-app-surface">
        <div className="h-full rounded-full bg-app-accent transition-all" style={{ width: `${progress}%` }} />
      </div>

      <div className="mt-4 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-6">
        <RunMetric label="Accepted" value={acceptedDocuments} />
        <RunMetric label="Rejected" value={rejectedDocuments} tone={rejectedDocuments > 0 ? "danger" : "neutral"} />
        <RunMetric label="Queued" value={counts.pending_upload + counts.uploaded + counts.queued} tone="warning" />
        <RunMetric label="Extracting" value={counts.extracting} tone="warning" />
        <RunMetric label="Indexing" value={counts.indexing} tone="warning" />
        <RunMetric label="Ready" value={counts.ready + counts.indexed} tone="success" />
      </div>

      <div className="mt-4 flex flex-wrap gap-2 text-xs text-app-muted">
        {run ? (
          <span className="rounded-lg border border-app-border bg-app-surface px-2.5 py-1">
            Run status: <span className="font-medium text-app-text">{run.status}</span>
          </span>
        ) : null}
        <span className="rounded-lg border border-app-border bg-app-surface px-2.5 py-1">
          Queue: <span className="font-medium text-app-text">{queues.queued}</span> waiting,{" "}
          <span className="font-medium text-app-text">{queues.running}</span> running
        </span>
        {queues.failed > 0 ? (
          <span className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1 text-red-700">
            Queue failures: {queues.failed}
          </span>
        ) : null}
        {statusError ? (
          <span className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1 text-red-700">
            {statusError}
          </span>
        ) : null}
      </div>
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  icon,
  tone = "neutral",
}: {
  label: string;
  value: number;
  icon: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return (
    <div
      className={cn(
        "rounded-xl border px-3 py-2",
        tone === "success" ? "border-green-200 bg-green-50" : "",
        tone === "warning" ? "border-amber-200 bg-amber-50" : "",
        tone === "danger" ? "border-red-200 bg-red-50" : "",
        tone === "neutral" ? "border-app-border bg-app-surface" : "",
      )}
    >
      <div className="flex items-center gap-2 text-app-muted">
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <p className="mt-1 text-lg font-semibold text-app-text">{value}</p>
    </div>
  );
}

function RunMetric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return (
    <div
      className={cn(
        "rounded-xl border px-3 py-2",
        tone === "success" ? "border-green-200 bg-green-50" : "",
        tone === "warning" ? "border-amber-200 bg-amber-50" : "",
        tone === "danger" ? "border-red-200 bg-red-50" : "",
        tone === "neutral" ? "border-app-border bg-app-surface" : "",
      )}
    >
      <p className="text-xs font-medium text-app-muted">{label}</p>
      <p className="mt-1 text-base font-semibold text-app-text">{value}</p>
    </div>
  );
}

function DocumentSection({
  title,
  count,
  emptyText,
  action,
  children,
}: {
  title: string;
  count: number;
  emptyText: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-app-text">{title}</h3>
          <span className="rounded-full border border-app-border bg-app-surface px-2 py-0.5 text-xs text-app-muted">{count}</span>
        </div>
        {action}
      </div>
      <div className="space-y-2">
        {count > 0 ? children : <p className="rounded-xl border border-app-border bg-app-surface px-4 py-5 text-sm text-app-muted">{emptyText}</p>}
      </div>
    </section>
  );
}

function DocumentRow({
  document,
  deleting,
  retrying,
  onRetry,
  onDelete,
}: {
  document: DocumentRecord;
  deleting: boolean;
  retrying: boolean;
  onRetry?: () => void;
  onDelete: () => void;
}) {
  const durations = ingestionDurations(document);

  return (
    <article className="rounded-xl border border-app-border bg-white px-4 py-3">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_170px_170px_auto] lg:items-center">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-app-text">{document.filename}</p>
          <p className="mt-1 text-xs text-app-muted">{statusHelp(document.status)}</p>
          {document.error_message ? <p className="mt-1 line-clamp-2 text-xs text-app-danger">{document.error_message}</p> : null}
        </div>

        <div>
          <span className={cn("inline-flex rounded-full border px-2 py-0.5 text-xs font-medium", statusBadge(document.status))}>
            {statusLabel(document.status)}
          </span>
        </div>

        <div className="text-xs text-app-muted">
          <p>{document.page_count == null ? "Pages --" : `${document.page_count} pages`}</p>
          <p className="mt-1">Updated {formatDate(document.updated_at)}</p>
        </div>

        <div className="flex flex-wrap gap-2 lg:justify-end">
          {onRetry ? (
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-lg border border-app-border bg-white px-2.5 py-1.5 text-xs font-medium text-app-text hover:border-app-accent disabled:cursor-not-allowed disabled:opacity-60"
              onClick={onRetry}
              disabled={retrying}
            >
              <RotateCcw size={13} className={retrying ? "animate-spin" : ""} />
              {retrying ? "Retrying" : "Retry"}
            </button>
          ) : null}
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-lg border border-red-200 bg-white px-2.5 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
            onClick={onDelete}
            disabled={deleting}
          >
            <Trash2 size={13} />
            {deleting ? "Deleting" : "Delete"}
          </button>
        </div>
      </div>
      {durations.length > 0 ? (
        <div className="mt-3 grid gap-2 border-t border-app-border pt-3 text-xs sm:grid-cols-2 lg:grid-cols-5">
          {durations.map((item) => (
            <div key={item.label} className="rounded-lg bg-app-surface px-2.5 py-2">
              <p className="font-medium text-app-muted">{item.label}</p>
              <p className="mt-0.5 font-semibold text-app-text">{item.value}</p>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}
