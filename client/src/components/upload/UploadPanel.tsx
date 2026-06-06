import {
  AlertTriangle,
  CheckCircle2,
  File,
  Loader2,
  Play,
  UploadCloud,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DragEventHandler } from "react";

import {
  apiUploadCompleteBatch,
  apiUploadPrepareBatch,
  apiUploadToSignedUrl,
  type DocumentRecord,
  type DocumentStatus,
  type UploadCompleteBatchFile,
} from "../../lib/api";
import { cn } from "../../lib/utils";

const MAX_FILES = 50;
const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024;
const CONCURRENCY = 4;

type UploadTaskState =
  | "selected"
  | "rejected"
  | "accepted"
  | "uploading"
  | "completing"
  | "queued"
  | "extracting"
  | "indexing"
  | "ready"
  | "failed";

type UploadTask = {
  id: string;
  file: File;
  state: UploadTaskState;
  progress: number;
  message?: string;
  documentId?: string;
  bucket?: string;
  storagePath?: string;
  uploadUrl?: string;
};

type UploadPanelProps = {
  token: string;
  onAfterUpload: () => Promise<void>;
  documents: DocumentRecord[];
  onRunCreated?: (runId: string) => void;
};

type PreparedTask = {
  taskId: string;
  documentId: string;
  bucket: string;
  storagePath: string;
  uploadUrl: string;
};

function clientFileId(file: File, index: number): string {
  const safeName = file.name.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 36);
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `${Date.now()}-${index}-${safeName}-${randomPart}`;
}

function fileSizeLabel(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(bytes / 1024, 1).toFixed(0)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function validateFile(file: File): string | null {
  const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) {
    return "Only PDF files are supported.";
  }

  if (file.size > MAX_FILE_SIZE_BYTES) {
    return `PDF must be ${fileSizeLabel(MAX_FILE_SIZE_BYTES)} or smaller.`;
  }

  return null;
}

function statusLabel(status: UploadTaskState): string {
  if (status === "selected") return "Ready to upload";
  if (status === "rejected") return "Rejected";
  if (status === "accepted") return "Prepared";
  if (status === "uploading") return "Uploading";
  if (status === "completing") return "Finalizing upload";
  if (status === "extracting") return "Extracting text";
  if (status === "indexing") return "Indexing";
  if (status === "ready") return "Ready";
  if (status === "failed") return "Failed";
  return "Queued";
}

function mapDocumentStatus(status: DocumentStatus): UploadTaskState {
  if (status === "indexed" || status === "ready") return "ready";
  if (status === "extracting") return "extracting";
  if (status === "indexing") return "indexing";
  if (status === "failed") return "failed";
  if (status === "uploaded" || status === "queued") return "queued";
  return "accepted";
}

function progressForState(state: UploadTaskState): number {
  if (state === "selected" || state === "rejected" || state === "failed") return 0;
  if (state === "accepted") return 15;
  if (state === "uploading") return 45;
  if (state === "completing") return 75;
  if (state === "queued") return 82;
  if (state === "extracting") return 90;
  if (state === "indexing") return 96;
  if (state === "ready") return 100;
  return 0;
}

function taskTone(state: UploadTaskState): string {
  if (state === "ready") {
    return "border-green-200 bg-green-50";
  }
  if (state === "failed" || state === "rejected") {
    return "border-red-200 bg-red-50";
  }
  if (state === "selected") {
    return "border-app-border bg-app-surface";
  }
  return "border-amber-200 bg-amber-50";
}

async function runWithConcurrency<T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> {
  let index = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (index < items.length) {
      const current = items[index];
      index += 1;
      await worker(current);
    }
  });

  await Promise.all(runners);
}

export default function UploadPanel({
  token,
  onAfterUpload,
  documents,
  onRunCreated,
}: UploadPanelProps) {
  const [tasks, setTasks] = useState<UploadTask[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [runState, setRunState] = useState<"idle" | "preparing" | "uploading" | "processing">("idle");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedCount = useMemo(() => tasks.filter((task) => task.state === "selected").length, [tasks]);
  const rejectedCount = useMemo(() => tasks.filter((task) => task.state === "rejected").length, [tasks]);
  const activeUploadCount = useMemo(
    () => tasks.filter((task) => ["accepted", "uploading", "completing", "queued", "extracting", "indexing"].includes(task.state)).length,
    [tasks],
  );
  const readyCount = useMemo(() => tasks.filter((task) => task.state === "ready").length, [tasks]);
  const failedCount = useMemo(() => tasks.filter((task) => task.state === "failed").length, [tasks]);
  const isRunning = runState === "preparing" || runState === "uploading";

  const updateTask = useCallback((id: string, patch: Partial<UploadTask>) => {
    setTasks((current) => current.map((task) => (task.id === id ? { ...task, ...patch } : task)));
  }, []);

  useEffect(() => {
    if (documents.length === 0) {
      return;
    }

    const documentById = new Map(documents.map((doc) => [doc.id, doc]));
    setTasks((current) =>
      current.map((task) => {
        if (!task.documentId) {
          return task;
        }

        const document = documentById.get(task.documentId);
        if (!document) {
          return task;
        }

        const nextState = mapDocumentStatus(document.status);
        return {
          ...task,
          state: nextState,
          progress: progressForState(nextState),
          message: document.error_message ?? task.message,
        };
      }),
    );
  }, [documents]);

  const addFiles = (incoming: FileList | File[]) => {
    const incomingFiles = Array.from(incoming);
    if (incomingFiles.length === 0) {
      return;
    }

    setError(null);
    setTasks((current) => {
      const activeSelected = current.filter((task) => task.state !== "rejected").length;
      let remainingSlots = Math.max(MAX_FILES - activeSelected, 0);
      const additions: UploadTask[] = incomingFiles.map((file, index) => {
        const id = clientFileId(file, index);
        const validationError = validateFile(file);
        if (validationError) {
          return {
            id,
            file,
            state: "rejected",
            progress: 0,
            message: validationError,
          };
        }

        if (remainingSlots <= 0) {
          return {
            id,
            file,
            state: "rejected",
            progress: 0,
            message: `This upload run supports up to ${MAX_FILES} files.`,
          };
        }

        remainingSlots -= 1;
        return {
          id,
          file,
          state: "selected",
          progress: 0,
        };
      });

      return [...current, ...additions];
    });
  };

  const startBatchUpload = async () => {
    const filesToPrepare = tasks.filter((task) => task.state === "selected");
    if (filesToPrepare.length === 0 || isRunning) {
      return;
    }

    setError(null);
    setRunState("preparing");

    try {
      const prepared = await apiUploadPrepareBatch(token, {
        name: `Upload run ${new Date().toLocaleString()}`,
        files: filesToPrepare.map((task) => ({
          filename: task.file.name,
          content_type: task.file.type || "application/pdf",
          file_size_bytes: task.file.size,
          idempotency_key: task.id,
          client_file_id: task.id,
        })),
      });

      setRunId(prepared.ingestion_run_id);
      onRunCreated?.(prepared.ingestion_run_id);

      const preparedTasks: PreparedTask[] = [];
      const taskByClientId = new Map(filesToPrepare.map((task) => [task.id, task]));

      prepared.items.forEach((item) => {
        const task = item.client_file_id ? taskByClientId.get(item.client_file_id) : filesToPrepare[item.index];
        if (!task) {
          return;
        }

        if ((item.status === "prepared" || item.status === "accepted") && item.document_id && item.bucket && item.storage_path && item.upload_url) {
          preparedTasks.push({
            taskId: task.id,
            documentId: item.document_id,
            bucket: item.bucket,
            storagePath: item.storage_path,
            uploadUrl: item.upload_url,
          });
          updateTask(task.id, {
            state: "accepted",
            progress: progressForState("accepted"),
            documentId: item.document_id,
            bucket: item.bucket,
            storagePath: item.storage_path,
            uploadUrl: item.upload_url,
            message: undefined,
          });
          return;
        }

        updateTask(task.id, {
          state: "rejected",
          progress: 0,
          message: item.error ?? "The server rejected this file.",
        });
      });

      if (preparedTasks.length === 0) {
        setRunState("idle");
        await onAfterUpload();
        return;
      }

      setRunState("uploading");
      const completedFiles: UploadCompleteBatchFile[] = [];
      const documentIdToTaskId = new Map<string, string>();

      await runWithConcurrency(preparedTasks, CONCURRENCY, async (preparedTask) => {
        const task = filesToPrepare.find((item) => item.id === preparedTask.taskId);
        if (!task) {
          return;
        }

        try {
          updateTask(preparedTask.taskId, { state: "uploading", progress: progressForState("uploading") });
          await apiUploadToSignedUrl(preparedTask.uploadUrl, task.file);
          updateTask(preparedTask.taskId, { state: "completing", progress: progressForState("completing") });
          completedFiles.push({
            document_id: preparedTask.documentId,
            bucket: preparedTask.bucket,
            storage_path: preparedTask.storagePath,
          });
          documentIdToTaskId.set(preparedTask.documentId, preparedTask.taskId);
        } catch (uploadError) {
          updateTask(preparedTask.taskId, {
            state: "failed",
            progress: 0,
            message: uploadError instanceof Error ? uploadError.message : "Storage upload failed.",
          });
        }
      });

      if (completedFiles.length > 0) {
        const completed = await apiUploadCompleteBatch(token, {
          ingestion_run_id: prepared.ingestion_run_id,
          files: completedFiles,
        });

        completed.items.forEach((item) => {
          const taskId = documentIdToTaskId.get(item.document_id);
          if (!taskId) {
            return;
          }

          if (item.error || item.status === "failed") {
            updateTask(taskId, {
              state: "failed",
              progress: 0,
              message: item.error ?? "Upload completion failed.",
            });
            return;
          }

          updateTask(taskId, {
            state: "queued",
            progress: progressForState("queued"),
            message: "Queued for extraction",
          });
        });
      }

      setRunState("processing");
      await onAfterUpload();
    } catch (err) {
      setRunState("idle");
      setError(err instanceof Error ? err.message : "Batch upload failed.");
    }
  };

  const clearFinished = () => {
    setTasks((current) => current.filter((task) => !["ready", "rejected", "failed"].includes(task.state)));
  };

  const onDrop: DragEventHandler<HTMLLabelElement> = (event) => {
    event.preventDefault();
    addFiles(event.dataTransfer.files);
  };

  return (
    <section className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-app-text">Bulk document upload</h2>
          <p className="mt-1 text-sm text-app-muted">Select up to {MAX_FILES} PDFs, review the batch, then start one ingestion run.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {tasks.length > 0 ? (
            <button
              type="button"
              className="btn-secondary gap-1.5"
              onClick={clearFinished}
              disabled={isRunning}
            >
              <X size={15} />
              Clear finished
            </button>
          ) : null}
          <button
            type="button"
            className="btn-secondary gap-1.5"
            onClick={() => inputRef.current?.click()}
            disabled={isRunning}
          >
            <UploadCloud size={15} />
            Browse files
          </button>
        </div>
      </div>

      <label
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrop}
        className="mt-5 flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-app-border bg-app-surface px-5 py-10 text-center hover:border-app-accent"
      >
        <UploadCloud className="text-app-accent" size={28} />
        <p className="mt-3 text-sm font-medium text-app-text">Drag and drop PDFs here</p>
        <p className="mt-1 text-xs text-app-muted">Files over {fileSizeLabel(MAX_FILE_SIZE_BYTES)} or non-PDF files are rejected before upload.</p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          className="hidden"
          disabled={isRunning}
          onChange={(event) => {
            if (event.target.files) {
              addFiles(event.target.files);
              event.target.value = "";
            }
          }}
        />
      </label>

      <div className="mt-5 flex flex-wrap gap-2 text-xs">
        <Metric label="Selected" value={selectedCount} />
        <Metric label="Rejected" value={rejectedCount} tone={rejectedCount > 0 ? "danger" : "neutral"} />
        <Metric label="Processing" value={activeUploadCount} tone={activeUploadCount > 0 ? "warning" : "neutral"} />
        <Metric label="Ready" value={readyCount} tone={readyCount > 0 ? "success" : "neutral"} />
        <Metric label="Failed" value={failedCount} tone={failedCount > 0 ? "danger" : "neutral"} />
        {runId ? <Metric label="Run" value={runId.slice(0, 8)} /> : null}
      </div>

      {error ? <p className="mt-3 text-sm text-app-danger">{error}</p> : null}

      <div className="mt-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <p className="text-sm text-app-muted">
          {runState === "preparing"
            ? "Preparing signed upload URLs..."
            : runState === "uploading"
              ? "Uploading accepted files..."
              : runState === "processing"
                ? "Backend ingestion is running. Status will refresh automatically."
                : selectedCount > 0
                  ? `${selectedCount} files are ready to start.`
                  : "No files selected yet."}
        </p>
        <button
          type="button"
          className="btn-primary gap-2"
          onClick={() => void startBatchUpload()}
          disabled={selectedCount === 0 || isRunning}
        >
          {isRunning ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
          Start ingestion
        </button>
      </div>

      <div className="mt-5 space-y-2">
        {tasks.map((task) => (
          <article key={task.id} className={cn("rounded-xl border p-3", taskTone(task.state))}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-app-text">
                  <File size={14} className="mr-1 inline text-app-muted" />
                  {task.file.name}
                </p>
                <p className="mt-1 text-xs text-app-muted">
                  {statusLabel(task.state)} - {fileSizeLabel(task.file.size)}
                </p>
              </div>

              {task.state === "ready" ? <CheckCircle2 size={16} className="mt-0.5 flex-none text-app-success" /> : null}
              {task.state === "failed" || task.state === "rejected" ? (
                <AlertTriangle size={16} className="mt-0.5 flex-none text-app-danger" />
              ) : null}
            </div>

            <div className="mt-2 h-2 rounded-full bg-white">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  task.state === "failed" || task.state === "rejected"
                    ? "bg-app-danger"
                    : task.state === "ready"
                      ? "bg-app-success"
                      : "bg-app-accent",
                )}
                style={{ width: `${task.progress}%` }}
              />
            </div>

            {task.message ? (
              <p className={cn("mt-1 text-xs", task.state === "failed" || task.state === "rejected" ? "text-app-danger" : "text-app-muted")}>
                {task.message}
              </p>
            ) : null}
          </article>
        ))}

        {tasks.length === 0 ? <p className="text-sm text-app-muted">No files queued yet.</p> : null}
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 font-medium",
        tone === "success" ? "border-green-200 bg-green-50 text-green-700" : "",
        tone === "warning" ? "border-amber-200 bg-amber-50 text-amber-700" : "",
        tone === "danger" ? "border-red-200 bg-red-50 text-red-700" : "",
        tone === "neutral" ? "border-app-border bg-app-surface text-app-muted" : "",
      )}
    >
      <span>{label}</span>
      <span className="text-app-text">{value}</span>
    </span>
  );
}
