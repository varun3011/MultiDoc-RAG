import { Save, SendHorizontal, StopCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ChatSessionList from "../components/chat/ChatSessionList";
import type { CitationChip } from "../components/chat/CitationsBar";
import SourcesPanel from "../components/chat/SourcesPanel";
import Toast, { type ToastState } from "../components/Toast";
import { useAppShellContext } from "../components/layout/AppShell";
import { useAuth } from "../context/AuthContext";
import {
  ApiError,
  apiCreateChatSession,
  apiGetChatSession,
  apiGetCitation,
  apiListChatSessions,
  apiQueryStream,
  apiUpdateChatSession,
  type ChatSessionListItem,
  type ChatSessionMessage,
  type CitationSource,
  type DocumentRecord,
  type QueryCitation,
} from "../lib/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: CitationChip[];
  failed?: boolean;
  ts: string;
};

const draftStorageKey = "enterprise-rag:chat-session-draft";
const maxQueryDocuments = 10;
const maxQueryPages = 100;
type QueryMode = "selected" | "all_ready";

function toTranscript(messages: Message[]): ChatSessionMessage[] {
  return messages.map((message) => ({
    role: message.role,
    content: message.text,
    ts: message.ts,
    citations: message.citations as Array<Record<string, unknown>>,
  }));
}

function fromTranscript(messages: ChatSessionMessage[]): Message[] {
  return messages.map((message, index) => ({
    id: `restored-${index}-${new Date(message.ts).getTime() || Date.now()}`,
    role: message.role,
    text: message.content,
    citations: Array.isArray(message.citations)
      ? message.citations
          .map((item) => {
            const chunkId = String(item.chunk_id ?? "");
            const documentId = String(item.document_id ?? "");
            const pageNumber = Number(item.page_number ?? 0);
            if (!chunkId || !documentId || !pageNumber) {
              return null;
            }
            return {
              chunk_id: chunkId,
              document_id: documentId,
              page_number: pageNumber,
            };
          })
          .filter((item): item is CitationChip => Boolean(item))
      : [],
    ts: message.ts,
  }));
}

function buildTitle(messages: Message[]): string {
  const firstUser = messages.find((message) => message.role === "user" && message.text.trim().length > 0);
  if (!firstUser) {
    return "Untitled chat";
  }
  return firstUser.text.trim().slice(0, 120);
}

function normalizeCitations(citations: QueryCitation[]): CitationChip[] {
  return citations.map((item) => ({
    chunk_id: item.chunk_id,
    page_number: item.page_number,
    document_id: item.document_id,
  }));
}

export default function ChatPage() {
  const { activeDocument, documents, selectedDocumentIds, setSelectedDocumentIds, setActiveDocumentId, setUsageToday } =
    useAppShellContext();
  const { accessToken } = useAuth();

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [queryMode, setQueryMode] = useState<QueryMode>("selected");
  const [panelOpen, setPanelOpen] = useState(false);
  const [loadingSource, setLoadingSource] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [source, setSource] = useState<CitationSource | null>(null);
  const [loadingChunkId, setLoadingChunkId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  const [sessions, setSessions] = useState<ChatSessionListItem[]>([]);
  const [sessionsTotal, setSessionsTotal] = useState(0);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [currentTitle, setCurrentTitle] = useState("Untitled chat");
  const [savingSession, setSavingSession] = useState(false);

  const activeControllerRef = useRef<AbortController | null>(null);
  const activeStreamIdRef = useRef<string | null>(null);
  const suppressDocumentResetRef = useRef(false);
  const persistBestEffortRef = useRef<() => void>(() => undefined);

  const readyDocuments = useMemo(() => documents.filter((doc) => doc.status === "indexed" || doc.status === "ready"), [documents]);
  const selectedDocuments = useMemo(
    () =>
      selectedDocumentIds
        .map((documentId) => documents.find((doc) => doc.id === documentId))
        .filter((doc): doc is DocumentRecord => Boolean(doc)),
    [documents, selectedDocumentIds],
  );
  const queryDocuments = queryMode === "all_ready" ? readyDocuments : selectedDocuments;
  const queryDocumentIds = useMemo(() => queryDocuments.map((doc) => doc.id), [queryDocuments]);
  const queryPageCount = queryDocuments.reduce((total, doc) => total + Number(doc.page_count ?? 0), 0);
  const queryCapError =
    queryDocumentIds.length === 0
      ? "Select at least one indexed document."
      : queryDocumentIds.length > maxQueryDocuments
        ? `Query supports up to ${maxQueryDocuments} documents.`
        : queryPageCount > maxQueryPages
          ? `Selected documents have ${queryPageCount} pages; query supports up to ${maxQueryPages} pages.`
          : null;
  const primaryDocument = queryDocuments[0] ?? activeDocument;

  const refreshSessions = useCallback(async () => {
    if (!accessToken) {
      return;
    }
    setSessionsLoading(true);
    try {
      const response = await apiListChatSessions(accessToken, { limit: 50, offset: 0 });
      setSessions(response.items);
      setSessionsTotal(response.total);
    } catch {
      setSessions([]);
      setSessionsTotal(0);
    } finally {
      setSessionsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    if (!accessToken) {
      return;
    }
    void refreshSessions();
  }, [accessToken, refreshSessions]);

  useEffect(() => {
    if (queryMode !== "selected" || selectedDocumentIds.length === 0) {
      return;
    }

    if (suppressDocumentResetRef.current) {
      suppressDocumentResetRef.current = false;
      return;
    }

    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    activeStreamIdRef.current = null;
    setPanelOpen(false);
    setLoadingSource(false);
    setSourceError(null);
    setSource(null);
    setLoadingChunkId(null);
    setMessages([]);
    setCurrentSessionId(null);
    setCurrentTitle("Untitled chat");

    const raw = localStorage.getItem(draftStorageKey);
    if (!raw) {
      return;
    }

    try {
      const parsed = JSON.parse(raw) as {
        sessionId: string | null;
        title: string;
        documentId: string | null;
        documentIds?: string[];
        queryMode?: QueryMode;
        messages: ChatSessionMessage[];
      };
      const restoredDocumentIds = parsed.documentIds?.length ? parsed.documentIds : parsed.documentId ? [parsed.documentId] : [];
      if (restoredDocumentIds.length > 0 && restoredDocumentIds.some((documentId) => !selectedDocumentIds.includes(documentId))) {
        return;
      }
      if (Array.isArray(parsed.messages) && parsed.messages.length > 0) {
        setMessages(fromTranscript(parsed.messages));
        setCurrentSessionId(parsed.sessionId);
        setCurrentTitle(parsed.title || "Untitled chat");
        setQueryMode(parsed.queryMode === "all_ready" ? "all_ready" : "selected");
      }
    } catch {
      localStorage.removeItem(draftStorageKey);
    }
  }, [queryMode, selectedDocumentIds]);

  useEffect(() => {
    const payload = {
      sessionId: currentSessionId,
      title: currentTitle,
      documentId: queryDocumentIds[0] ?? null,
      documentIds: queryDocumentIds,
      queryMode,
      messages: toTranscript(messages),
    };
    localStorage.setItem(draftStorageKey, JSON.stringify(payload));
  }, [currentSessionId, currentTitle, messages, queryDocumentIds, queryMode]);

  const persistCurrentSession = useCallback(
    async (ended: boolean): Promise<string | null> => {
      if (!accessToken || messages.length === 0) {
        return currentSessionId;
      }

      const transcript = toTranscript(messages);
      const title = buildTitle(messages);
      setCurrentTitle(title);

      if (!currentSessionId) {
        const created = await apiCreateChatSession(accessToken, {
          document_id: queryDocumentIds[0] ?? null,
          document_ids: queryDocumentIds,
          title,
          messages: transcript,
        });

        if (ended) {
          await apiUpdateChatSession(accessToken, created.id, {
            document_id: queryDocumentIds[0] ?? null,
            document_ids: queryDocumentIds,
            title,
            messages: transcript,
            ended: true,
          });
        }

        setCurrentSessionId(created.id);
        return created.id;
      }

      await apiUpdateChatSession(accessToken, currentSessionId, {
        document_id: queryDocumentIds[0] ?? null,
        document_ids: queryDocumentIds,
        title,
        messages: transcript,
        ended,
      });
      return currentSessionId;
    },
    [accessToken, currentSessionId, messages, queryDocumentIds],
  );

  useEffect(() => {
    persistBestEffortRef.current = () => {
      if (!accessToken || messages.length === 0) {
        return;
      }

      const endpoint = currentSessionId ? `/chats/sessions/${currentSessionId}` : "/chats/sessions";
      const method = currentSessionId ? "PATCH" : "POST";
      const body = currentSessionId
        ? {
            document_id: queryDocumentIds[0] ?? null,
            document_ids: queryDocumentIds,
            title: buildTitle(messages),
            messages: toTranscript(messages),
            ended: true,
          }
        : {
            document_id: queryDocumentIds[0] ?? null,
            document_ids: queryDocumentIds,
            title: buildTitle(messages),
            messages: toTranscript(messages),
          };

      void fetch(`${import.meta.env.VITE_API_URL ?? "http://localhost:8000"}${endpoint}`, {
        method,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify(body),
        keepalive: true,
      });
    };
  }, [accessToken, currentSessionId, messages, queryDocumentIds]);

  useEffect(() => {
    const handleBeforeUnload = () => {
      persistBestEffortRef.current();
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      persistBestEffortRef.current();
    };
  }, []);

  const handleCitationClick = useCallback(
    async (citation: CitationChip) => {
      if (!accessToken) {
        return;
      }
      setPanelOpen(true);
      setLoadingSource(true);
      setSourceError(null);
      setLoadingChunkId(citation.chunk_id);
      try {
        const data = await apiGetCitation(accessToken, citation.chunk_id);
        setSource(data);
      } catch (error) {
        setSource(null);
        setSourceError(error instanceof ApiError ? error.message : "Failed to load source.");
      } finally {
        setLoadingSource(false);
        setLoadingChunkId(null);
      }
    },
    [accessToken],
  );

  const sendMessage = useCallback(async () => {
    const trimmed = input.trim();
    if (!trimmed || !accessToken) {
      return;
    }
    if (queryCapError) {
      setToast({ id: Date.now(), message: queryCapError, type: "error" });
      return;
    }

    activeControllerRef.current?.abort();

    const timestamp = new Date().toISOString();
    const userMessage: Message = {
      id: `u-${Date.now()}`,
      role: "user",
      text: trimmed,
      citations: [],
      ts: timestamp,
    };
    const assistantMessageId = `a-${Date.now()}`;
    const controller = new AbortController();
    activeControllerRef.current = controller;
    activeStreamIdRef.current = assistantMessageId;

    setMessages((current) => [
      ...current,
      userMessage,
      { id: assistantMessageId, role: "assistant", text: "", citations: [], failed: false, ts: new Date().toISOString() },
    ]);
    setInput("");
    let streamReportedError = false;

    try {
      await apiQueryStream(
        accessToken,
        {
          document_id: queryDocumentIds[0],
          document_ids: queryDocumentIds,
          question: trimmed,
        },
        {
          onDelta: (delta) => {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantMessageId ? { ...message, text: `${message.text}${delta}` } : message,
              ),
            );
          },
          onCitations: (citations) => {
            const chips = normalizeCitations(citations);
            setMessages((current) =>
              current.map((message) => (message.id === assistantMessageId ? { ...message, citations: chips } : message)),
            );
          },
          onUsage: (usage) => {
            setUsageToday(usage);
          },
          onError: (message) => {
            streamReportedError = true;
            setToast({ id: Date.now(), message, type: "error" });
          },
        },
        controller.signal,
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      const message = error instanceof ApiError ? error.message : "Query failed. Please try again.";
      if (!streamReportedError) {
        setToast({ id: Date.now(), message, type: "error" });
      }
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                failed: true,
                text: item.text ? `${item.text}\n\n[Stream failed] ${message}` : `[Stream failed] ${message}`,
              }
            : item,
        ),
      );
    } finally {
      if (activeStreamIdRef.current === assistantMessageId) {
        activeStreamIdRef.current = null;
        activeControllerRef.current = null;
      }
    }
  }, [accessToken, input, queryCapError, queryDocumentIds, setUsageToday]);

  const handleEndChat = useCallback(async () => {
    if (!accessToken) {
      return;
    }
    if (messages.length === 0) {
      setCurrentSessionId(null);
      setCurrentTitle("Untitled chat");
      return;
    }

    setSavingSession(true);
    try {
      await persistCurrentSession(true);
      await refreshSessions();
      setMessages([]);
      setCurrentSessionId(null);
      setCurrentTitle("Untitled chat");
      localStorage.removeItem(draftStorageKey);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Failed to save chat session.";
      setToast({ id: Date.now(), message, type: "error" });
    } finally {
      setSavingSession(false);
    }
  }, [accessToken, messages.length, persistCurrentSession, refreshSessions]);

  const handleOpenSession = useCallback(
    async (sessionId: string) => {
      if (!accessToken) {
        return;
      }

      setSavingSession(true);
      try {
        if (messages.length > 0) {
          await persistCurrentSession(true);
        }

        const detail = await apiGetChatSession(accessToken, sessionId);
        const sessionDocumentIds = detail.document_ids?.length ? detail.document_ids : detail.document_id ? [detail.document_id] : [];
        if (sessionDocumentIds.length > 0) {
          suppressDocumentResetRef.current = true;
          setSelectedDocumentIds(sessionDocumentIds);
          setActiveDocumentId(sessionDocumentIds[0]);
          setQueryMode("selected");
        }
        setCurrentSessionId(detail.id);
        setCurrentTitle(detail.title || "Untitled chat");
        setMessages(fromTranscript(detail.messages));
      } catch (error) {
        const message = error instanceof ApiError ? error.message : "Failed to load chat session.";
        setToast({ id: Date.now(), message, type: "error" });
      } finally {
        setSavingSession(false);
      }
    },
    [accessToken, messages.length, persistCurrentSession, setActiveDocumentId, setSelectedDocumentIds],
  );

  useEffect(() => {
    return () => {
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
      activeStreamIdRef.current = null;
    };
  }, []);

  const sourceDocumentName = useMemo(() => {
    if (!source?.document_id) {
      return primaryDocument?.filename ?? null;
    }
    const match = documents.find((doc) => doc.id === source.document_id);
    return match?.filename ?? primaryDocument?.filename ?? null;
  }, [documents, primaryDocument?.filename, source?.document_id]);

  if (readyDocuments.length === 0) {
    return (
      <div className="p-4 md:p-6">
        <section className="flex h-full min-h-[70vh] items-center justify-center rounded-2xl border border-app-border bg-white p-6 text-center">
          <div>
            <p className="text-lg font-semibold text-app-text">Upload and index a document to start chatting.</p>
            <p className="mt-2 text-sm text-app-muted">Only indexed documents can be queried.</p>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6">
      <section className="grid min-h-[72vh] grid-cols-1 gap-4 xl:grid-cols-[1fr_320px]">
        <div className="flex min-h-[72vh] flex-col rounded-2xl border border-app-border bg-white">
          <header className="border-b border-app-border px-5 py-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-[0.08em] text-app-muted">Document Chat</p>
                <h2 className="mt-1 text-lg font-semibold text-app-text">
                  {queryMode === "all_ready"
                    ? `All ready documents (${readyDocuments.length})`
                    : queryDocuments.length === 1
                      ? queryDocuments[0].filename
                      : `${queryDocuments.length} selected documents`}
                </h2>
                <p className="mt-1 text-sm text-app-muted">
                  {currentTitle} {currentSessionId ? `• session ${currentSessionId.slice(0, 8)}` : "• unsaved"}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className={queryMode === "selected" ? "btn-primary px-3 py-1.5 text-xs" : "btn-secondary px-3 py-1.5 text-xs"}
                    onClick={() => setQueryMode("selected")}
                  >
                    Selected documents
                  </button>
                  <button
                    type="button"
                    className={queryMode === "all_ready" ? "btn-primary px-3 py-1.5 text-xs" : "btn-secondary px-3 py-1.5 text-xs"}
                    onClick={() => setQueryMode("all_ready")}
                  >
                    All ready documents
                  </button>
                  <span className="text-xs text-app-muted">
                    {queryDocumentIds.length}/{maxQueryDocuments} docs, {queryPageCount}/{maxQueryPages} pages
                  </span>
                </div>
                {queryCapError ? <p className="mt-2 text-xs text-app-danger">{queryCapError}</p> : null}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="btn-secondary gap-2"
                  onClick={async () => {
                    if (messages.length === 0 || !accessToken) {
                      return;
                    }
                    setSavingSession(true);
                    try {
                      await persistCurrentSession(false);
                      await refreshSessions();
                    } catch (error) {
                      const message = error instanceof ApiError ? error.message : "Failed to save chat session.";
                      setToast({ id: Date.now(), message, type: "error" });
                    } finally {
                      setSavingSession(false);
                    }
                  }}
                  disabled={savingSession || messages.length === 0}
                >
                  <Save size={14} /> Save
                </button>
                <button
                  type="button"
                  className="btn-primary gap-2"
                  onClick={() => {
                    void handleEndChat();
                  }}
                  disabled={savingSession || messages.length === 0}
                >
                  <StopCircle size={14} /> End Chat
                </button>
              </div>
            </div>
          </header>

          <div className="flex-1 space-y-3 overflow-y-auto bg-app-surface px-5 py-4">
            {messages.length === 0 ? (
              <p className="text-sm text-app-muted">Ask a question about the selected document set to start the conversation.</p>
            ) : (
              messages.map((message) => (
                <article
                  key={message.id}
                  className={`max-w-[85%] rounded-2xl border px-3 py-2 text-sm ${
                    message.role === "user"
                      ? "ml-auto border-app-accent bg-app-accentSoft text-app-text"
                      : message.failed
                        ? "border-app-danger/70 bg-red-50 text-app-text"
                        : "border-app-border bg-white text-app-text"
                  }`}
                >
                  <p className="whitespace-pre-wrap">{message.text}</p>
                  {message.role === "assistant" ? (
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {message.citations.map((citation) => (
                        <button
                          key={citation.chunk_id}
                          type="button"
                          className="rounded-full border border-app-border bg-app-surface px-2 py-1 text-xs text-app-muted hover:border-app-accent hover:text-app-text"
                          disabled={loadingChunkId === citation.chunk_id}
                          onClick={() => {
                            void handleCitationClick(citation);
                          }}
                        >
                          p.{citation.page_number}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </article>
              ))
            )}
          </div>

          <div className="border-t border-app-border p-4">
            <div className="flex items-end gap-2">
              <textarea
                className="app-input min-h-[48px] resize-none"
                rows={2}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Ask a question about the selected documents"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
              />

              <button
                type="button"
                className="btn-primary h-[46px] w-[46px] flex-none p-0"
                onClick={() => void sendMessage()}
                disabled={Boolean(queryCapError)}
              >
                <SendHorizontal size={16} />
              </button>
            </div>
          </div>
        </div>

        <ChatSessionList
          items={sessions}
          total={sessionsTotal}
          selectedSessionId={currentSessionId}
          documents={documents}
          loading={sessionsLoading || savingSession}
          onSelect={(sessionId) => {
            void handleOpenSession(sessionId);
          }}
        />
      </section>

      <SourcesPanel
        open={panelOpen}
        loading={loadingSource}
        error={sourceError}
        source={source}
        documentName={sourceDocumentName}
        onClose={() => setPanelOpen(false)}
      />
      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
