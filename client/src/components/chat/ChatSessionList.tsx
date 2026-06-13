import { Clock3, MessageSquare } from "lucide-react";

import type { ChatSessionListItem, DocumentRecord } from "../../lib/api";
import { cn } from "../../lib/utils";

type ChatSessionListProps = {
  items: ChatSessionListItem[];
  total: number;
  selectedSessionId: string | null;
  documents: DocumentRecord[];
  loading: boolean;
  onSelect: (sessionId: string) => void;
};

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString();
}

export default function ChatSessionList({
  items,
  total,
  selectedSessionId,
  documents,
  loading,
  onSelect,
}: ChatSessionListProps) {
  return (
    <aside className="rounded-2xl border border-app-border bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs uppercase tracking-[0.08em] text-app-muted">Chats</p>
        <span className="rounded-full border border-app-border bg-app-surface px-2 py-0.5 text-xs text-app-muted">{total}</span>
      </div>
      {loading ? <p className="text-sm text-app-muted">Loading chats...</p> : null}
      {!loading && items.length === 0 ? <p className="text-sm text-app-muted">No saved chats yet.</p> : null}
      <div className="space-y-2">
        {items.map((item) => {
          const documentIds = item.document_ids?.length ? item.document_ids : item.document_id ? [item.document_id] : [];
          const firstDocName = documentIds[0]
            ? documents.find((doc) => doc.id === documentIds[0])?.filename ?? "Document"
            : "All documents";
          const docName = documentIds.length > 1 ? `${firstDocName} + ${documentIds.length - 1} more` : firstDocName;
          return (
            <button
              key={item.id}
              type="button"
              className={cn(
                "w-full rounded-xl border px-3 py-2 text-left transition",
                selectedSessionId === item.id
                  ? "border-app-accent bg-app-accentSoft"
                  : "border-app-border bg-app-surface hover:border-app-accent/60",
              )}
              onClick={() => onSelect(item.id)}
            >
              <p className="line-clamp-1 text-sm font-medium text-app-text">{item.title || "Untitled chat"}</p>
              <p className="mt-1 flex items-center gap-1 text-xs text-app-muted">
                <MessageSquare size={12} />
                {docName}
              </p>
              <p className="mt-1 flex items-center gap-1 text-xs text-app-muted">
                <Clock3 size={12} />
                {formatUpdatedAt(item.updated_at)}
              </p>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
