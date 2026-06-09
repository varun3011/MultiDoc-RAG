import { useEffect, useMemo, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { apiGetDocuments, apiGetObservability, type DocumentRecord, type ObservabilityResponse } from "../lib/api";

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
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

function formatDuration(ms: number | null): string {
  if (ms === null) {
    return "--";
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

function average(values: Array<number | null>): number | null {
  const valid = values.filter((value): value is number => value !== null);
  if (valid.length === 0) {
    return null;
  }
  return Math.round(valid.reduce((sum, value) => sum + value, 0) / valid.length);
}

function timingStats(documents: DocumentRecord[]) {
  const documentsWithTiming = documents.filter((document) => document.timing);
  const completed = documentsWithTiming.filter((document) => document.timing?.index_finished_at);
  const totalDurations = completed.map((document) =>
    durationMs(document.timing?.upload_completed_at, document.timing?.index_finished_at),
  );
  const uploadTimes = completed
    .map((document) => document.timing?.upload_completed_at)
    .filter((value): value is string => Boolean(value))
    .map((value) => new Date(value).getTime())
    .filter((value) => Number.isFinite(value));
  const finishTimes = completed
    .map((document) => document.timing?.index_finished_at)
    .filter((value): value is string => Boolean(value))
    .map((value) => new Date(value).getTime())
    .filter((value) => Number.isFinite(value));
  const batchWallMs =
    uploadTimes.length > 0 && finishTimes.length > 0
      ? Math.max(...finishTimes) - Math.min(...uploadTimes)
      : null;
  return {
    completed,
    avgExtractMs: average(
      completed.map((document) =>
        durationMs(document.timing?.extract_started_at, document.timing?.extract_finished_at),
      ),
    ),
    avgIndexMs: average(
      completed.map((document) =>
        durationMs(document.timing?.index_started_at, document.timing?.index_finished_at),
      ),
    ),
    avgTotalMs: average(
      totalDurations,
    ),
    batchWallMs,
  };
}

export default function ObservabilityPage() {
  const { accessToken } = useAuth();
  const [data, setData] = useState<ObservabilityResponse | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) {
      return;
    }
    let active = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [response, recentDocuments] = await Promise.all([
          apiGetObservability(accessToken),
          apiGetDocuments(accessToken, { limit: 100 }),
        ]);
        if (active) {
          setData(response);
          setDocuments(recentDocuments);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load observability");
          setData(null);
          setDocuments([]);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [accessToken]);

  const maxVolume = useMemo(() => {
    if (!data || data.query_volume.length === 0) {
      return 1;
    }
    return Math.max(...data.query_volume.map((point) => point.count), 1);
  }, [data]);

  const ingestionTiming = useMemo(() => timingStats(documents), [documents]);

  if (loading) {
    return <div className="p-4 md:p-6 text-sm text-app-muted">Loading observability data...</div>;
  }

  if (error || !data) {
    return <div className="p-4 md:p-6 text-sm text-app-danger">Observability error: {error ?? "unknown"}</div>;
  }

  return (
    <div className="space-y-5 p-4 md:p-6">
      <section className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
        <h2 className="text-xl font-semibold text-app-text">Observability</h2>
        <p className="mt-1 text-sm text-app-muted">
          Generated at {new Date(data.generated_at).toLocaleString()} (last {data.window_days} days)
        </p>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Total queries" value={data.query_summary.total_queries.toLocaleString()} />
        <Metric label="Queries (24h)" value={data.query_summary.queries_last_24h.toLocaleString()} />
        <Metric label="Errors (24h)" value={data.query_summary.error_count_last_24h.toLocaleString()} />
        <Metric label="Error rate (24h)" value={pct(data.query_summary.error_rate_last_24h)} />
        <Metric label="Avg latency (24h)" value={`${Math.round(data.query_summary.avg_latency_ms_last_24h)} ms`} />
        <Metric label="P95 latency (24h)" value={`${Math.round(data.query_summary.p95_latency_ms_last_24h)} ms`} />
        <Metric label="Tokens used today" value={data.usage_today.used.toLocaleString()} />
        <Metric label="Tokens remaining" value={data.usage_today.remaining.toLocaleString()} />
      </section>

      <section className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
        <h3 className="text-base font-semibold text-app-text">Query volume (last {data.window_days} days)</h3>
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-7">
          {data.query_volume.map((point) => {
            const bar = Math.max(8, Math.round((point.count / maxVolume) * 100));
            return (
              <div key={point.date} className="rounded-xl border border-app-border bg-app-surface p-3">
                <p className="text-xs text-app-muted">{point.date}</p>
                <div className="mt-2 h-2 rounded-full bg-white">
                  <div className="h-full rounded-full bg-app-accent" style={{ width: `${bar}%` }} />
                </div>
                <p className="mt-2 text-sm font-semibold text-app-text">{point.count} queries</p>
                <p className="text-xs text-app-muted">{point.errors} errors</p>
              </div>
            );
          })}
        </div>
      </section>

      <section className="grid gap-5 lg:grid-cols-2">
        <div className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
          <h3 className="text-base font-semibold text-app-text">Document pipeline health</h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Metric label="Total" value={data.documents.total.toString()} />
            <Metric label="Ready" value={data.documents.ready.toString()} />
            <Metric label="Processing" value={data.documents.processing.toString()} />
            <Metric label="Failed" value={data.documents.failed.toString()} />
          </div>
        </div>

        <div className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
          <h3 className="text-base font-semibold text-app-text">Top queried documents</h3>
          <div className="mt-4 space-y-2">
            {data.top_documents.length === 0 ? (
              <p className="text-sm text-app-muted">No query data yet.</p>
            ) : (
              data.top_documents.map((item) => (
                <div key={item.document_id} className="rounded-xl border border-app-border bg-app-surface p-3">
                  <p className="truncate text-sm font-semibold text-app-text">{item.filename}</p>
                  <p className="mt-1 text-xs text-app-muted">
                    {item.query_count} queries, {item.error_count} errors
                  </p>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h3 className="text-base font-semibold text-app-text">Ingestion timing</h3>
            <p className="text-sm text-app-muted">Recent completed documents with stage timing.</p>
          </div>
          <p className="text-xs text-app-muted">{ingestionTiming.completed.length} completed documents sampled</p>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Avg extraction" value={formatDuration(ingestionTiming.avgExtractMs)} />
          <Metric label="Avg indexing" value={formatDuration(ingestionTiming.avgIndexMs)} />
          <Metric label="Avg total ingestion" value={formatDuration(ingestionTiming.avgTotalMs)} />
          <Metric label="Batch wall time" value={formatDuration(ingestionTiming.batchWallMs)} />
        </div>

        <div className="mt-4 space-y-2">
          {ingestionTiming.completed.length === 0 ? (
            <p className="rounded-xl border border-app-border bg-app-surface p-3 text-sm text-app-muted">
              No completed ingestion timing data yet.
            </p>
          ) : (
            ingestionTiming.completed.map((document) => (
              <div key={document.id} className="rounded-xl border border-app-border bg-app-surface p-3">
                <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                  <p className="truncate text-sm font-semibold text-app-text">{document.filename}</p>
                  <p className="text-xs text-app-muted">{document.status}</p>
                </div>
                <div className="mt-2 grid gap-2 text-xs sm:grid-cols-3">
                  <TimingValue
                    label="Extract"
                    value={durationMs(document.timing?.extract_started_at, document.timing?.extract_finished_at)}
                  />
                  <TimingValue
                    label="Index"
                    value={durationMs(document.timing?.index_started_at, document.timing?.index_finished_at)}
                  />
                  <TimingValue
                    label="Total"
                    value={durationMs(document.timing?.upload_completed_at, document.timing?.index_finished_at)}
                  />
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-app-border bg-white p-5 md:p-6">
        <h3 className="text-base font-semibold text-app-text">Recent query errors</h3>
        <div className="mt-4 space-y-2">
          {data.recent_errors.length === 0 ? (
            <p className="text-sm text-app-muted">No recent query errors.</p>
          ) : (
            data.recent_errors.map((item) => (
              <div key={item.query_id} className="rounded-xl border border-app-border bg-app-surface p-3">
                <p className="text-xs text-app-muted">{new Date(item.created_at).toLocaleString()}</p>
                <p className="mt-1 text-sm font-semibold text-app-text">{item.question}</p>
                <p className="mt-1 text-xs text-app-danger">{item.error_message}</p>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-app-border bg-white p-4">
      <p className="text-xs uppercase tracking-[0.08em] text-app-muted">{label}</p>
      <p className="mt-2 text-sm font-semibold text-app-text">{value}</p>
    </div>
  );
}

function TimingValue({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="rounded-lg bg-white px-2.5 py-2">
      <p className="font-medium text-app-muted">{label}</p>
      <p className="mt-0.5 font-semibold text-app-text">{formatDuration(value)}</p>
    </div>
  );
}
