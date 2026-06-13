import { HardDrive, LogOut, MessageSquare, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useOutletContext } from "react-router-dom";

import { useAuth } from "../../context/AuthContext";
import {
  apiGetDocuments,
  apiGetUsageToday,
  apiGetWorkspaceMe,
  apiReindexDocument,
  apiRetryDocument,
  type DocumentRecord,
  type UsageToday,
  type WorkspaceMe,
} from "../../lib/api";
import { cn } from "../../lib/utils";
import DocumentSidebar from "../sidebar/DocumentSidebar";

export type AppShellContextValue = {
  workspace: WorkspaceMe | null;
  documents: DocumentRecord[];
  loading: boolean;
  activeDocument: DocumentRecord | null;
  selectedDocumentIds: string[];
  setSelectedDocumentIds: (documentIds: string[]) => void;
  setActiveDocumentId: (documentId: string | null) => void;
  setUsageToday: (usage: UsageToday) => void;
  refreshWorkspace: () => Promise<void>;
  refreshDocuments: () => Promise<void>;
};

const activeDocumentStorageKey = "enterprise-rag:active-document";
const selectedDocumentsStorageKey = "enterprise-rag:selected-documents";

function restoreSelectedDocumentIds(): string[] {
  try {
    const raw = localStorage.getItem(selectedDocumentsStorageKey);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.map((item) => String(item)).filter(Boolean) : [];
  } catch {
    localStorage.removeItem(selectedDocumentsStorageKey);
    return [];
  }
}

export default function AppShell() {
  const { accessToken, user, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [workspace, setWorkspace] = useState<WorkspaceMe | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [sidebarQuery, setSidebarQuery] = useState("");
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(() => localStorage.getItem(activeDocumentStorageKey));
  const [selectedDocumentIds, setSelectedDocumentIdsState] = useState<string[]>(restoreSelectedDocumentIds);
  const [loading, setLoading] = useState(true);
  const [refreshingUsage, setRefreshingUsage] = useState(false);

  const setSelectedDocumentIds = useCallback((documentIds: string[]) => {
    const unique = Array.from(new Set(documentIds.filter(Boolean)));
    setSelectedDocumentIdsState(unique);
    setActiveDocumentId(unique[0] ?? null);
  }, []);

  const setActiveDocumentSelection = useCallback((documentId: string | null) => {
    setActiveDocumentId(documentId);
    setSelectedDocumentIdsState(documentId ? [documentId] : []);
  }, []);

  const refreshWorkspace = useCallback(async () => {
    if (!accessToken) {
      return;
    }

    try {
      const nextWorkspace = await apiGetWorkspaceMe(accessToken);
      let usageToday: UsageToday = nextWorkspace.usage_today;

      try {
        usageToday = await apiGetUsageToday(accessToken);
      } catch {
        usageToday = nextWorkspace.usage_today;
      }

      setWorkspace({ ...nextWorkspace, usage_today: usageToday });
    } catch {
      navigate("/workspace", { replace: true });
    }
  }, [accessToken, navigate]);

  const refreshDocuments = useCallback(async () => {
    if (!accessToken) {
      return;
    }

    try {
      const nextDocuments = await apiGetDocuments(accessToken, { limit: 100 });
      setDocuments(nextDocuments);
    } catch {
      setDocuments([]);
    }
  }, [accessToken]);

  useEffect(() => {
    if (!accessToken) {
      return;
    }

    let active = true;

    const load = async () => {
      setLoading(true);
      await Promise.all([refreshWorkspace(), refreshDocuments()]);
      if (active) {
        setLoading(false);
      }
    };

    void load();

    return () => {
      active = false;
    };
  }, [accessToken, refreshWorkspace, refreshDocuments]);

  useEffect(() => {
    if (!activeDocumentId) {
      return;
    }

    localStorage.setItem(activeDocumentStorageKey, activeDocumentId);
  }, [activeDocumentId]);

  useEffect(() => {
    if (!activeDocumentId) {
      return;
    }

    const readyDocumentIds = new Set(
      documents.filter((doc) => doc.status === "indexed" || doc.status === "ready").map((doc) => doc.id),
    );
    const stillExists = readyDocumentIds.has(activeDocumentId);

    if (!stillExists) {
      setActiveDocumentId(null);
      localStorage.removeItem(activeDocumentStorageKey);
    }
    setSelectedDocumentIdsState((current) => current.filter((documentId) => readyDocumentIds.has(documentId)));
  }, [activeDocumentId, documents]);

  useEffect(() => {
    localStorage.setItem(selectedDocumentsStorageKey, JSON.stringify(selectedDocumentIds));
  }, [selectedDocumentIds]);

  const activeDocument = useMemo(
    () => documents.find((doc) => doc.id === activeDocumentId) ?? null,
    [activeDocumentId, documents],
  );

  const usage = workspace?.usage_today;
  const setUsageToday = useCallback((usageToday: UsageToday) => {
    setWorkspace((current) => (current ? { ...current, usage_today: usageToday } : current));
  }, []);

  const handleUsageRefresh = async () => {
    setRefreshingUsage(true);
    await refreshWorkspace();
    setRefreshingUsage(false);
  };

  const handleSelectDocument = (documentId: string) => {
    setSelectedDocumentIds([documentId]);
    navigate("/app/chat");
  };

  const handleToggleDocument = (documentId: string) => {
    setSelectedDocumentIds(
      selectedDocumentIds.includes(documentId)
        ? selectedDocumentIds.filter((selectedId) => selectedId !== documentId)
        : [...selectedDocumentIds, documentId],
    );
    navigate("/app/chat");
  };

  const handleRetryDocument = async (documentId: string) => {
    if (!accessToken) {
      return;
    }
    try {
      await apiRetryDocument(accessToken, documentId);
      await Promise.all([refreshDocuments(), refreshWorkspace()]);
    } catch {
      // Keep sidebar responsive; document cards remain source of truth after refresh.
    }
  };

  const handleReindexDocument = async (documentId: string) => {
    if (!accessToken) {
      return;
    }
    try {
      await apiReindexDocument(accessToken, documentId);
      await Promise.all([refreshDocuments(), refreshWorkspace()]);
    } catch {
      // Keep sidebar responsive; document cards remain source of truth after refresh.
    }
  };

  const handleSignOut = async () => {
    await signOut();
    navigate("/login", { replace: true });
  };

  const contextValue: AppShellContextValue = {
    workspace,
    documents,
    loading,
    activeDocument,
    selectedDocumentIds,
    setSelectedDocumentIds,
    setActiveDocumentId: setActiveDocumentSelection,
    setUsageToday,
    refreshWorkspace,
    refreshDocuments,
  };

  return (
    <div className="flex min-h-screen bg-white text-app-text">
      <div className="hidden w-80 lg:block">
        <DocumentSidebar
          workspace={workspace}
          documents={documents}
          activeDocumentId={activeDocumentId}
          selectedDocumentIds={selectedDocumentIds}
          query={sidebarQuery}
          onQueryChange={setSidebarQuery}
          onSelectDocument={handleSelectDocument}
          onToggleDocument={handleToggleDocument}
          onGoUpload={() => navigate("/app/upload")}
          onRetryDocument={(documentId) => {
            void handleRetryDocument(documentId);
          }}
          onReindexDocument={(documentId) => {
            void handleReindexDocument(documentId);
          }}
        />
      </div>

      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-app-border bg-white">
          <div className="flex items-center justify-between gap-3 px-4 py-3 md:px-6">
            <div className="flex min-w-0 items-center gap-2">
              <HardDrive size={16} className="text-app-accent" />
              <h1 className="truncate text-sm font-semibold md:text-base">{workspace?.name ?? "Workspace"}</h1>
            </div>

            <nav className="hidden items-center gap-2 md:flex">
              <TopNav to="/app/upload" label="Upload" />
              <TopNav to="/app/chat" label="Chat" />
              <TopNav to="/app/observability" label="Observability" />
              <TopNav to="/app/workspace" label="Workspace" />
            </nav>

            <div className="flex items-center gap-2 md:gap-3">
              <button
                type="button"
                onClick={handleUsageRefresh}
                className="inline-flex items-center gap-1 rounded-lg border border-app-border bg-white px-2.5 py-1.5 text-xs font-medium text-app-muted hover:border-app-accent"
                disabled={refreshingUsage}
              >
                <RefreshCw size={13} className={cn(refreshingUsage ? "animate-spin" : "")} />
                Refresh
              </button>

              <div className="rounded-lg border border-app-border bg-app-surface px-2.5 py-1.5 text-xs">
                {usage ? (
                  <span>
                    {usage.used.toLocaleString()}/{usage.limit.toLocaleString()} used
                  </span>
                ) : (
                  <span className="text-app-muted">Usage --</span>
                )}
              </div>

              <button
                type="button"
                onClick={handleSignOut}
                className="inline-flex items-center gap-1 rounded-lg border border-app-border bg-white px-2.5 py-1.5 text-xs font-medium hover:border-app-accent"
                title={user?.email ?? "Sign out"}
              >
                <LogOut size={13} />
                Sign out
              </button>
            </div>
          </div>

          <nav className="flex items-center gap-2 border-t border-app-border px-4 py-2 md:hidden">
            <TopNav to="/app/upload" label="Upload" />
            <TopNav to="/app/chat" label="Chat" icon={<MessageSquare size={14} />} />
            <TopNav to="/app/observability" label="Observability" />
            <TopNav to="/app/workspace" label="Workspace" />
          </nav>
        </header>

        <main className="min-w-0 flex-1 bg-white">
          <Outlet context={contextValue} />
        </main>

        {location.pathname.startsWith("/app") ? (
          <div className="border-t border-app-border p-3 text-center text-xs text-app-muted lg:hidden">
            Documents are available in desktop sidebar.
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TopNav({ to, label, icon }: { to: string; label: string; icon?: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition",
          isActive
            ? "border-app-accent bg-app-accentSoft text-app-text"
            : "border-app-border bg-white text-app-muted hover:border-app-accent",
        )
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}

export function useAppShellContext() {
  return useOutletContext<AppShellContextValue>();
}
