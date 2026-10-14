import React from "react";
import { Layers, Download, CheckCheck, LogOut, ShieldCheck } from "lucide-react";
import { monthLabel } from "../lib/format";

export default function Header({ summary, user, onExport, onCommit, onLogout, committing }) {
  const canWrite = user.role === "planner" || user.role === "admin";
  const canCommit = canWrite && summary && !summary.committed && summary.pending_decisions === 0;
  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur px-8 py-4 flex justify-between items-center sticky top-0 z-50">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-3">
          <Layers className="text-indigo-400 h-6 w-6" />
          UK Demand Planning S&amp;OP Workbench
        </h1>
        {summary && (
          <p className="text-xs text-slate-400 mt-0.5">
            Last actuals: <span className="text-slate-200 font-semibold">{monthLabel(summary.cutoff)}</span> | Planning from{" "}
            <span className="text-slate-200 font-semibold">{monthLabel(summary.first_month)}</span> | Signed in as{" "}
            <span className="text-indigo-300 font-semibold">{user.username}</span> ({user.role})
            {summary.committed && <span className="ml-2 text-emerald-400 font-semibold">Cycle committed</span>}
          </p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 bg-slate-800/80 px-3 py-1.5 rounded-lg border border-slate-700 text-xs">
          <ShieldCheck className="h-4 w-4 text-emerald-400" />
          <span>Audit ledger: insert-only</span>
        </div>
        <button onClick={onExport} className="px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-semibold rounded-lg flex items-center gap-2">
          <Download className="h-4 w-4" /> Export draft plan
        </button>
        {canWrite && (
          <button onClick={onCommit} disabled={!canCommit || committing}
            title={canCommit ? "Commit the consensus plan" : "Decide every exception first"}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold rounded-lg flex items-center gap-2">
            <CheckCheck className="h-4 w-4" /> Commit consensus
          </button>
        )}
        <button onClick={onLogout} aria-label="Sign out" className="p-2 text-slate-400 hover:text-white">
          <LogOut className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
