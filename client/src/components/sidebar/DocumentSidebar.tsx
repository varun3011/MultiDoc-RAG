import { Check, FileText, Search, UploadCloud } from "lucide-react";
import type { ReactNode } from "react";

import type { DocumentRecord, WorkspaceMe } from "../../lib/api";
import { cn } from "../../lib/utils";

type SidebarProps = {
  workspace: WorkspaceMe | null;
  documents: DocumentRecord[];
  activeDocumentId: string | null;
  selectedDocumentIds: string[];
  query: string;
  onQueryChange: (value: string) => void;
  onSelectDocument: (documentId: string) => void;
  onToggleDocument: (documentId: string) => void;
  onGoUpload: () => void;
  onRetryDocument: (documentId: string) => void;
  onReindexDocument: (documentId: string) => void;
};

const processingStatuses = new Set(["pending_upload", "uploaded", "queued", "extracting", "indexing"]);

function statusLabel(status: DocumentRecord["status"]): string {
  if (status === "indexed" || status === "ready") {
    return "Indexed";
  }

  if (status === "failed") {
    return "Failed";
  }

  if (status === "extracting") {
    return "Extracting";
  }

  if (status === "indexing") {
    return "Indexing";
  }

  if (status === "queued") {
    return "Queued";
  }

  if (status === "pending_upload") {
    return "Pending upload";
  }

  return "Uploaded";
}

function statusDotClass(status: DocumentRecord["status"]): string {
  if (status === "indexed" || status === "ready") {
    return "bg-app-success";
  }

  if (status === "failed") {
    return "bg-app-danger";
  }

  return "bg-app-warning";
}

export default function DocumentSidebar({
  workspace,
  documents,
  activeDocumentId,
  selectedDocumentIds,
  query,
  onQueryChange,
  onSelectDocument,
  onToggleDocument,
  onGoUpload,
  onRetryDocument,
  onReindexDocument,
}: SidebarProps) {
  const filtered = documents.filter((doc) => doc.filename.toLowerCase().includes(query.toLowerCase()));

  const ready = filtered.filter((doc) => doc.status === "indexed" || doc.status === "ready");
  const processing = filtered.filter((doc) => processingStatuses.has(doc.status));
  const failed = filtered.filter((doc) => doc.status === "failed");

  return (
    <aside className="flex h-full w-full flex-col border-r border-app-border bg-app-surface">
      <div className="border-b border-app-border px-4 py-4">
        <p className="text-xs font-medium uppercase tracking-[0.1em] text-app-muted">Workspace</p>
        <h2 className="mt-1 truncate text-base font-semibold text-app-text">{workspace?.name ?? "Loading..."}</h2>
      </div>

      <div className="border-b border-app-border p-4">
        <button type="button" className="btn-primary w-full gap-2" onClick={onGoUpload}>
          <UploadCloud size={16} />
          Upload documents
        </button>

        <label className="mt-3 flex items-center gap-2 rounded-xl border border-app-border bg-white px-3 py-2">
          <Search size={14} className="text-app-muted" />
          <input
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Search documents"
            className="w-full border-none bg-transparent p-0 text-sm text-app-text outline-none placeholder:text-app-muted"
          />
        </label>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-4">
        <Section title="Ready" count={ready.length}>
          {ready.map((doc) => (
            <DocRow
              key={doc.id}
              document={doc}
              active={activeDocumentId === doc.id}
              selected={selectedDocumentIds.includes(doc.id)}
              onClick={() => onSelectDocument(doc.id)}
              onToggle={() => onToggleDocument(doc.id)}
              onReindex={() => onReindexDocument(doc.id)}
            />
          ))}
        </Section>

        <Section title="Processing" count={processing.length}>
          {processing.map((doc) => (
            <DocRow key={doc.id} document={doc} active={false} disabled />
          ))}
        </Section>

        <Section title="Failed" count={failed.length}>
          {failed.map((doc) => (
            <DocRow key={doc.id} document={doc} active={false} onRetry={() => onRetryDocument(doc.id)} />
          ))}
        </Section>
      </div>

      <div className="border-t border-app-border px-4 py-3 text-xs text-app-muted">{documents.length} documents</div>
    </aside>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <section className="mb-5">
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="text-xs font-semibold uppercase tracking-[0.08em] text-app-muted">{title}</h3>
        <span className="rounded-full border border-app-border bg-white px-2 py-0.5 text-xs text-app-muted">{count}</span>
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function DocRow({
  document,
  active,
  selected,
  onClick,
  onToggle,
  disabled,
  onRetry,
  onReindex,
}: {
  document: DocumentRecord;
  active: boolean;
  selected?: boolean;
  onClick?: () => void;
  onToggle?: () => void;
  disabled?: boolean;
  onRetry?: () => void;
  onReindex?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "w-full rounded-xl border px-3 py-2.5 text-left transition",
        active
          ? "border-app-accent bg-app-accentSoft"
          : "border-app-border bg-white hover:border-app-accent/60 hover:bg-app-accentSoft/50",
        disabled ? "cursor-not-allowed opacity-75 hover:border-app-border hover:bg-white" : "",
      )}
    >
      <div className="flex items-start gap-2">
        {onToggle ? (
          <span
            role="checkbox"
            aria-checked={selected}
            tabIndex={0}
            className={cn(
              "mt-0.5 flex h-4 w-4 flex-none items-center justify-center rounded border text-[10px] font-semibold",
              selected ? "border-app-accent bg-app-accent text-white" : "border-app-border bg-white text-transparent",
            )}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                event.stopPropagation();
                onToggle();
              }
            }}
          >
            <Check size={11} />
          </span>
        ) : (
          <FileText size={16} className="mt-0.5 flex-none text-app-muted" />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-app-text">{document.filename}</p>
          <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-app-muted">
            <span className={cn("h-2 w-2 rounded-full", statusDotClass(document.status))} />
            {statusLabel(document.status)}
          </p>
        </div>
      </div>
      {document.status === "failed" && onRetry ? (
        <div className="mt-2">
          <span
            role="button"
            tabIndex={0}
            className="rounded-md border border-app-border bg-white px-2 py-1 text-xs font-medium text-app-text hover:border-app-accent"
            onClick={(event) => {
              event.stopPropagation();
              onRetry();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                event.stopPropagation();
                onRetry();
              }
            }}
          >
            Retry
          </span>
        </div>
      ) : null}
      {(document.status === "indexed" || document.status === "ready") && onReindex ? (
        <div className="mt-2">
          <span
            role="button"
            tabIndex={0}
            className="rounded-md border border-app-border bg-white px-2 py-1 text-xs font-medium text-app-text hover:border-app-accent"
            onClick={(event) => {
              event.stopPropagation();
              onReindex();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                event.stopPropagation();
                onReindex();
              }
            }}
          >
            Reindex
          </span>
        </div>
      ) : null}
    </button>
  );
}
