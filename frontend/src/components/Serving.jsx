import React from "react";
import { DataTable } from "./ui";
import { fmtNum } from "../lib/format";

const bytes = (n) => {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

const ms = (n) => (n == null ? "n/a" : `${fmtNum(n, 0)} ms`);

const age = (s) => {
  if (s == null) return "unknown";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} d`;
};

const ratio = (r) => (r == null ? "n/a" : `${(r * 100).toFixed(0)}%`);

const verdict = (ok) => {
  if (ok === null || ok === undefined) return { label: "unmeasured", tone: "text-slate-400" };
  return ok
    ? { label: "within SLA", tone: "text-emerald-300" }
    : { label: "breach", tone: "text-rose-300" };
};

export default function Serving({ data, transport, onMeasure, onDownload, busy }) {
  if (!data) {
    return <div className="text-xs text-slate-400" data-testid="bi-serving-empty">No serving report yet.</div>;
  }
  const { freshness = {}, latency = {}, indexes = {}, gaps = [] } = data;
  const fresh = verdict(freshness.ok);
  const latencyBreaches = latency.breaches || [];

  return (
    <div className="space-y-4" data-testid="bi-serving">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs" data-testid="bi-serving-summary">
        <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Freshness</div>
          <div className={`font-semibold ${fresh.tone}`} data-testid="bi-serving-freshness">
            {fresh.label}
          </div>
          <div className="text-slate-500 mt-0.5" data-testid="bi-serving-age">{age(freshness.age_s)} old</div>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Indexes</div>
          <div className={`font-semibold ${indexes.ok ? "text-emerald-300" : "text-amber-300"}`}
            data-testid="bi-serving-indexes">
            {(indexes.present || []).length}/{(indexes.declared || []).length}
          </div>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Latency breaches</div>
          <div className={`font-semibold ${latencyBreaches.length ? "text-rose-300" : "text-emerald-300"}`}
            data-testid="bi-serving-latency-breaches">
            {latencyBreaches.length}
          </div>
        </div>
      </div>

      <div>
        <h4 className="text-xs font-semibold text-slate-300 mb-1">Freshness tiers</h4>
        <DataTable testId="bi-serving-freshness-table" rows={freshness.tables || []} empty="No tiers declared."
          columns={[
            { key: "table", label: "Table", render: (r) => <span className="font-mono text-slate-200">{r.table}</span> },
            { key: "tier", label: "Tier" },
            { key: "sla_s", label: "SLA", right: true, render: (r) => age(r.sla_s) },
            { key: "age_s", label: "Age", right: true, render: (r) => age(r.age_s) },
            {
              key: "breach", label: "State", render: (r) => (r.present
                ? (r.breach ? <span className="text-rose-300">stale</span> : <span className="text-emerald-300">fresh</span>)
                : <span className="text-slate-500">absent</span>),
            },
          ]} />
      </div>

      <div>
        <h4 className="text-xs font-semibold text-slate-300 mb-1">Workload latency against the persona SLA</h4>
        <DataTable testId="bi-serving-latency-table" rows={latency.workloads || []} empty="No workloads declared."
          columns={[
            { key: "workload", label: "Workload" },
            { key: "persona", label: "Persona" },
            { key: "target_p95_ms", label: "Target p95", right: true, render: (r) => ms(r.target_p95_ms) },
            { key: "measured_p95_ms", label: "Measured p95", right: true, render: (r) => ms(r.measured_p95_ms) },
            { key: "samples", label: "Samples", right: true, render: (r) => fmtNum(r.samples, 0) },
            {
              key: "ok", label: "State",
              render: (r) => <span className={verdict(r.ok).tone}>{verdict(r.ok).label}</span>,
            },
          ]} />
      </div>

      {(indexes.missing || []).length > 0 && (
        <p className="text-[11px] text-amber-300" data-testid="bi-serving-missing-indexes">
          Missing indexes: {(indexes.missing || []).join(", ")}
        </p>
      )}

      <div>
        <div className="flex items-center justify-between mb-1">
          <h4 className="text-xs font-semibold text-slate-300">Columnar transport</h4>
          <button type="button" data-testid="bi-transport-measure" disabled={busy} onClick={onMeasure}
            className="rounded border border-indigo-700 bg-indigo-950 px-3 py-1.5 text-[11px] font-semibold text-indigo-200 disabled:opacity-50">
            {busy ? "Measuring..." : "Measure"}
          </button>
        </div>
        {!transport && (
          <p className="text-[11px] text-slate-400" data-testid="bi-transport-hint">
            Encodes every mart table as JSON rows, Arrow IPC and Parquet and reports the bytes and time for each.
          </p>
        )}
        {transport && (
          <>
            <p className="text-[11px] text-slate-300 mb-1" data-testid="bi-transport-totals">
              {fmtNum(transport.totals.rows, 0)} rows: JSON {bytes(transport.totals.json_bytes)}, Arrow{" "}
              {bytes(transport.totals.arrow_bytes)} ({ratio(transport.totals.arrow_ratio)}), Parquet{" "}
              {bytes(transport.totals.parquet_bytes)} ({ratio(transport.totals.parquet_ratio)}).
            </p>
            <DataTable testId="bi-transport-table" rows={transport.tables || []} empty="No tables measured."
              columns={[
                { key: "table", label: "Table", render: (r) => <span className="font-mono text-slate-200">{r.table}</span> },
                { key: "rows", label: "Rows", right: true, render: (r) => fmtNum(r.rows, 0) },
                { key: "json_bytes", label: "JSON", right: true, render: (r) => bytes(r.json_bytes) },
                { key: "arrow_bytes", label: "Arrow", right: true, render: (r) => `${bytes(r.arrow_bytes)} (${ratio(r.arrow_ratio)})` },
                { key: "parquet_bytes", label: "Parquet", right: true, render: (r) => `${bytes(r.parquet_bytes)} (${ratio(r.parquet_ratio)})` },
                {
                  key: "download", label: "Export",
                  render: (r) => (
                    <span className="flex gap-2">
                      <button type="button" data-testid={`bi-download-arrow-${r.table}`}
                        onClick={() => onDownload(r.table, "arrow")}
                        className="text-indigo-300 underline">arrow</button>
                      <button type="button" data-testid={`bi-download-parquet-${r.table}`}
                        onClick={() => onDownload(r.table, "parquet")}
                        className="text-indigo-300 underline">parquet</button>
                    </span>
                  ),
                },
              ]} />
          </>
        )}
      </div>

      {gaps.length > 0 && (
        <ul className="text-[11px] text-slate-400 list-disc pl-4" data-testid="bi-serving-gaps">
          {gaps.map((gap) => <li key={gap}>{gap}</li>)}
        </ul>
      )}
    </div>
  );
}
