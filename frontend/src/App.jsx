import React, { useCallback, useEffect, useState } from "react";
import Login from "./components/Login";
import Header from "./components/Header";
import SummaryCards from "./components/SummaryCards";
import QualityBanner from "./components/QualityBanner";
import ExceptionTable from "./components/ExceptionTable";
import DetailPanel from "./components/DetailPanel";
import ScorecardPage from "./pages/ScorecardPage";
import SupplyRiskPage from "./pages/SupplyRiskPage";
import OperationsPage from "./pages/OperationsPage";
import CopilotPage from "./pages/CopilotPage";
import ScenariosPage from "./pages/ScenariosPage";
import { api, getStoredUser, getToken, onUnauthorized } from "./services/api";

const DEFAULT_FILTERS = { state: "EXCEPTION_REVIEW", channel: "", risk_type: "", pending: "", limit: 50, offset: 0 };

export default function App() {
  const [user, setUser] = useState(getToken() ? getStoredUser() : null);
  const [summary, setSummary] = useState(null);
  const [queue, setQueue] = useState(null);
  const [reasonCodes, setReasonCodes] = useState(null);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [committing, setCommitting] = useState(false);
  const [page, setPage] = useState("workbench");
  const [scenarioPrompt, setScenarioPrompt] = useState("");

  const logout = useCallback(() => {
    api.logout();
    setUser(null);
    setSummary(null);
    setQueue(null);
    setSelectedId(null);
  }, []);

  useEffect(() => onUnauthorized(logout), [logout]);

  const refreshQueue = useCallback(async () => {
    setLoading(true);
    try {
      const q = await api.exceptions(filters);
      setQueue(q);
      setError(null);
      setSelectedId((cur) => (cur === null && q.items.length ? q.items[0].item_id : cur));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const refreshSummary = useCallback(async () => {
    try {
      setSummary(await api.summary());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    refreshSummary();
    api.reasonCodes().then(setReasonCodes).catch((e) => setError(e.message));
  }, [user, refreshSummary]);

  useEffect(() => {
    if (user) refreshQueue();
  }, [user, refreshQueue]);

  const onChanged = () => {
    refreshSummary();
    refreshQueue();
  };

  const download = async (fetchBlob, name) => {
    try {
      const blob = await fetchBlob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e.message);
    }
  };
  const cutoff = summary ? summary.cutoff : "draft";
  const exportDraft = () => download(api.exportDraft, `consensus_${cutoff}_draft.csv`);
  const briefing = () => download(api.briefing, `sop_briefing_${cutoff}.pdf`);

  const commit = async () => {
    if (!summary || !window.confirm("Commit the consensus plan? Decisions will be closed for this cycle.")) return;
    setCommitting(true);
    try {
      const r = await api.commit(summary.cutoff);
      setNotice(`Consensus committed: ${r.items.toLocaleString("en-GB")} items written to ${r.path}`);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setCommitting(false);
    }
  };

  const navigateAction = (action) => {
    if (action.type === "scenario_prompt") {
      setScenarioPrompt(action.prompt);
      setPage("scenarios");
      setNotice("Scenario prompt loaded. Generate an editable draft when ready.");
      return;
    }
    if (action.type === "apply_autopilot") {
      setPage("scorecard");
      setNotice("Opened FVA & audit. Review the autopilot recommendation before applying.");
      return;
    }
    if (action.type === "review_phasing") {
      setPage("supply");
      setNotice("Opened Supply & risk for delivery phasing review.");
      return;
    }
    if (action.filters) {
      setFilters({ ...DEFAULT_FILTERS, ...action.filters });
      setPage("workbench");
      setNotice("Opened the workbench queue filtered to the selected risk flag.");
      return;
    }
    if (action.item_id) {
      setSelectedId(action.item_id);
      setPage("workbench");
      setNotice(`Opened item ${action.item_id} in the workbench.`);
    }
  };

  if (!user) return <Login onLoggedIn={setUser} />;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      <Header summary={summary} user={user} onExport={exportDraft} onCommit={commit} onLogout={logout} committing={committing}
        page={page} onPage={setPage} onBriefing={briefing} />
      {summary && <QualityBanner warnings={summary.quality_warnings} />}
      {notice && <div className="mx-8 mt-4 bg-emerald-950/50 border border-emerald-800 text-emerald-200 rounded-lg px-4 py-3 text-xs">{notice}</div>}
      {error && <div role="alert" className="mx-8 mt-4 bg-rose-950/50 border border-rose-800 text-rose-200 rounded-lg px-4 py-3 text-xs">{error}</div>}
      {page === "scorecard" && <ScorecardPage role={user.role} />}
      {page === "supply" && <SupplyRiskPage onNavigateAction={navigateAction} />}
      {page === "operations" && <OperationsPage role={user.role} onReloaded={onChanged} onNavigateAction={navigateAction} />}
      {page === "copilot" && <CopilotPage onNavigateAction={navigateAction} />}
      {page === "scenarios" && <ScenariosPage user={user} summary={summary} initialPrompt={scenarioPrompt} />}
      {page === "workbench" && <SummaryCards summary={summary} />}
      {page === "workbench" && <main className="flex-1 px-8 pb-8 grid grid-cols-1 lg:grid-cols-12 gap-6">
        <ExceptionTable data={queue} filters={filters} onFilters={setFilters} selectedId={selectedId} onSelect={setSelectedId} loading={loading} />
        <section className="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-xl p-5">
          {selectedId !== null && reasonCodes ? (
            <DetailPanel key={selectedId} itemId={selectedId} reasonCodes={reasonCodes} role={user.role} onChanged={onChanged} />
          ) : (
            <div className="flex-1 flex items-center justify-center text-slate-500 text-xs h-full">Select an item from the queue to start the review.</div>
          )}
          <div className="text-[11px] text-slate-500 pt-3 mt-4 border-t border-slate-800 text-center">
            All decisions are written to the insert-only audit ledger and feed the rolling FVA scorecard.
          </div>
        </section>
      </main>}
    </div>
  );
}
