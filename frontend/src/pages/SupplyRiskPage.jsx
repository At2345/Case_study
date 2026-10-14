import React, { useEffect, useState } from "react";
import { api } from "../services/api";
import { fmtNum, fmtGbp, fmtGbpPlain, monthLabel } from "../lib/format";
import { Panel, Stat, DataTable, ErrorNote } from "../components/ui";

const pct = (v) => (v === null || v === undefined ? "-" : `${(v * 100).toFixed(1)}%`);

function SafetySummary({ s }) {
  const a = s.a_items;
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
      <Stat testId="ss-a-dynamic" label="A-items dynamic SS" value={fmtNum(a.ss_dynamic_units)} sub="units, from P10-P90 spread" tone="text-emerald-300" />
      <Stat label="A-items static SS" value={fmtNum(a.ss_static_units)} sub="units, 12-month demand sigma" />
      <Stat testId="ss-a-holding" label="Holding cost (A)" value={fmtGbpPlain(a.holding_dynamic_gbp)} sub={`vs ${fmtGbpPlain(a.holding_static_gbp)} static, ${s.holding_rate * 100}% p.a.`} />
      <Stat testId="ss-a-reduction" label="Holding cost change" value={pct(a.reduction_pct)}
        sub={`target ${s.target_reduction_pct * 100}% ${s.target_met_on_a_items ? "(met)" : "(not met)"}`}
        tone={s.target_met_on_a_items ? "text-emerald-400" : "text-amber-400"} />
      {s.service_backtest && (
        <>
          <Stat testId="ss-service-dynamic" label="Achieved service, dynamic" value={pct(s.service_backtest.service_dynamic)}
            sub={`A-items, h1, cutoff ${s.service_backtest.cutoff}, target ${pct(s.service_backtest.target_service)}`} tone="text-emerald-300" />
          <Stat testId="ss-service-static" label="Achieved service, static" value={pct(s.service_backtest.service_static)}
            sub={`${fmtNum(s.service_backtest.rows)} item-months; bands fitted in-sample`} />
        </>
      )}
    </div>
  );
}

export default function SupplyRiskPage() {
  const [ss, setSs] = useState(null);
  const [otif, setOtif] = useState(null);
  const [channel, setChannel] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    api.safetyStock({ channel, abc_category: "", limit: 25 }).then(setSs).catch((e) => setError(e.message));
  }, [channel]);
  useEffect(() => { api.otif(50).then(setOtif).catch((e) => setError(e.message)); }, []);

  return (
    <div className="px-8 py-6 space-y-6" data-testid="supply-risk-page">
      <ErrorNote error={error} />
      <Panel title="Dynamic safety stock (Phase 3)" testId="safety-stock-panel"
        subtitle="SS = z x sqrt(LT x sigma_forecast^2 + demand^2 x sigma_LT^2). Sigma comes from the conformal P10-P90 band; lead time from order-book ordered_at to delivered_at plus a monthly review period. Achieved service is back-tested on the latest backtest cutoff.">
        {ss && !ss.available && <div className="text-xs text-slate-500">Run the monthly cycle to compute the bands and safety stock.</div>}
        {ss && ss.available && (
          <div className="space-y-5">
            <SafetySummary s={ss.summary} />
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div>
                <h3 className="text-xs font-semibold text-slate-300 mb-2">Band calibration (out-of-sample, last cutoff)</h3>
                <DataTable testId="coverage-table" rows={ss.coverage}
                  columns={[{ key: "channel", label: "Channel" }, { key: "hb", label: "Horizon" },
                    { key: "rows", label: "Rows", right: true, render: (r) => fmtNum(r.rows) },
                    { key: "coverage", label: "P10-P90 cover", right: true, render: (r) => pct(r.coverage) }]} />
                <p className="text-[10px] text-slate-500 mt-2">Target 80%. Higher means the bands are conservative (wide).</p>
                <h3 className="text-xs font-semibold text-slate-300 mt-5 mb-2">Lead times (order book)</h3>
                <DataTable rows={ss.summary.lead_times}
                  columns={[{ key: "channel", label: "Channel" },
                    { key: "lt_days_mean", label: "Mean days", right: true, render: (r) => fmtNum(r.lt_days_mean, 1) },
                    { key: "lt_days_std", label: "Std days", right: true, render: (r) => fmtNum(r.lt_days_std, 1) }]} />
              </div>
              <div className="lg:col-span-2">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs font-semibold text-slate-300">Largest holding-cost savings ({fmtNum(ss.total)} items)</h3>
                  <select data-testid="ss-channel-select" value={channel} onChange={(e) => setChannel(e.target.value)}
                    className="bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-200">
                    <option value="">All channels</option><option value="amazon">Amazon</option>
                    <option value="B2B wo Amazon">B2B</option><option value="D2C">D2C</option>
                  </select>
                </div>
                <DataTable testId="ss-items-table" rows={ss.items}
                  columns={[{ key: "item_id", label: "Item" }, { key: "channel", label: "Channel" }, { key: "abc_category", label: "ABC" },
                    { key: "final_m1", label: "Plan M1", right: true, render: (r) => fmtNum(r.final_m1) },
                    { key: "band", label: "P10-P90", right: true, render: (r) => `${fmtNum(r.p10)}-${fmtNum(r.p90)}` },
                    { key: "ss_static", label: "SS static", right: true, render: (r) => fmtNum(r.ss_static) },
                    { key: "ss_dynamic", label: "SS dynamic", right: true, render: (r) => fmtNum(r.ss_dynamic) },
                    { key: "saving", label: "Saving p.a.", right: true, render: (r) => fmtGbp(r.saving) }]} />
              </div>
            </div>
          </div>
        )}
      </Panel>
      <Panel title="OTIF exposure (F-B2B-03)" testId="otif-panel"
        subtitle={otif ? `${otif.note} Terms: fill below ${otif.terms.default.min_fill * 100}% of booked units costs ${otif.terms.default.penalty_pct * 100}% of the shortfall value (${otif.terms.source}).` : ""}>
        {otif && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <Stat testId="otif-items-at-risk" label="Item-months at risk" value={fmtNum(otif.items_at_risk)} tone="text-amber-300" />
              <Stat testId="otif-total-penalty" label="Estimated penalties" value={fmtGbpPlain(otif.total_penalty_gbp)} tone="text-rose-300" />
              <Stat label="Fix" value="Raise plan" sub="use 'Raise plan to order book' in the workbench" />
            </div>
            <DataTable testId="otif-table" rows={otif.rows} empty="Every booked order is covered by the consensus plan."
              columns={[{ key: "item_id", label: "Item" }, { key: "product_id", label: "Product" },
                { key: "month", label: "Month", render: (r) => monthLabel(r.month) },
                { key: "booked", label: "Booked", right: true, render: (r) => fmtNum(r.booked) },
                { key: "plan", label: "Plan", right: true, render: (r) => fmtNum(r.plan) },
                { key: "fill_rate", label: "Fill", right: true, render: (r) => pct(r.fill_rate) },
                { key: "penalty_gbp", label: "Penalty", right: true, render: (r) => fmtGbpPlain(r.penalty_gbp) }]} />
          </div>
        )}
      </Panel>
    </div>
  );
}
