import React from "react";
import { DataTable } from "./ui";
import { fmtNum } from "../lib/format";

const bytes = (n) => {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

const ms = (n) => `${fmtNum(n, 0)} ms`;

export default function FinOps({ data }) {
  if (!data) {
    return <div className="text-xs text-slate-400" data-testid="bi-finops-empty">No attribution recorded yet.</div>;
  }
  const { attribution = {}, storage = {}, thresholds = {}, sprawl = {} } = data;
  const metrics = sprawl.metrics || {};
  const pipelines = sprawl.pipelines || {};
  const tiers = thresholds.tiers || [];

  return (
    <div className="space-y-4" data-testid="bi-finops">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs" data-testid="bi-finops-summary">
        <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Metric queries</div>
          <div className="text-slate-100 font-semibold" data-testid="bi-finops-metric-queries">
            {fmtNum(attribution.metric_queries, 0)}
          </div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Requests</div>
          <div className="text-slate-100 font-semibold">{fmtNum(attribution.requests, 0)}</div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Statements</div>
          <div className="text-slate-100 font-semibold">{fmtNum(attribution.statements, 0)}</div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-950 px-3 py-2">
          <div className="text-slate-400">Measured</div>
          <div className="text-slate-100 font-semibold">{ms(attribution.duration_ms)}</div>
        </div>
      </div>

      <div>
        <h4 className="text-xs font-semibold text-slate-300 mb-1">Attribution by team</h4>
        <DataTable testId="bi-finops-teams" rows={attribution.by_team || []} empty="No attributed queries yet."
          columns={[
            { key: "team", label: "Team" },
            { key: "metric_queries", label: "Metric queries", right: true },
            { key: "requests", label: "Requests", right: true },
            { key: "statements", label: "Statements", right: true },
            { key: "duration_ms", label: "Time", right: true, render: (r) => ms(r.duration_ms) },
          ]} />
      </div>

      <div>
        <h4 className="text-xs font-semibold text-slate-300 mb-1">
          Storage intelligence
          <span className="ml-2 font-normal text-slate-400" data-testid="bi-finops-storage-bytes">
            {bytes(storage.bytes)} on disk
          </span>
        </h4>
        <DataTable testId="bi-finops-storage" rows={storage.tables || []} empty="The mart has not been built."
          columns={[
            { key: "table", label: "Table", render: (r) => <span className="font-mono text-slate-200">{r.table}</span> },
            { key: "bytes", label: "Size", right: true, render: (r) => (r.bytes == null ? "n/a" : bytes(r.bytes)) },
          ]} />
      </div>

      <div>
        <h4 className="text-xs font-semibold text-slate-300 mb-1">Spend governance</h4>
        <DataTable testId="bi-finops-tiers" rows={tiers} empty="No tiers declared."
          columns={[
            { key: "id", label: "Tier" },
            { key: "period", label: "Period" },
            { key: "amount_usd", label: "Trigger", right: true, render: (r) => `$${fmtNum(r.amount_usd, 0)}` },
            { key: "obligation", label: "Obligation" },
          ]} />
        <p className="mt-1 text-[11px] text-amber-300" data-testid="bi-finops-thresholds-note">
          {thresholds.active
            ? `Active tier: ${thresholds.active}`
            : `No tier is active. ${thresholds.reason || ""}`}
        </p>
      </div>

      <div>
        <h4 className="text-xs font-semibold text-slate-300 mb-1">Sprawl review</h4>
        <p className="text-[11px] text-slate-300" data-testid="bi-finops-sprawl">
          {fmtNum(metrics.evaluated, 0)} of {fmtNum(metrics.total, 0)} metrics were evaluated in the last{" "}
          {fmtNum(sprawl.window_days, 0)} days; {fmtNum((metrics.prune_candidates || []).length, 0)} are prune
          candidates. Surfaces not called: {(pipelines.archive_candidates || []).join(", ") || "none"}.
        </p>
        {(metrics.prune_candidates || []).length > 0 && (
          <p className="mt-1 text-[11px] text-slate-400 font-mono" data-testid="bi-finops-prune-candidates">
            {(metrics.prune_candidates || []).join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}
