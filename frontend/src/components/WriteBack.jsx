import React, { useState } from "react";
import { Check, MessageSquare, X } from "lucide-react";
import { monthLabel } from "../lib/format";

const STATE_STYLES = {
  pending: "bg-amber-950 text-amber-300 border-amber-800",
  approved: "bg-emerald-950 text-emerald-300 border-emerald-800",
  rejected: "bg-rose-950 text-rose-300 border-rose-800",
};

const TARGETS = ["item", "segment", "metric", "cause"];

export function ActionQueue({ actions, canWrite, onDecide, busy }) {
  const rows = actions || [];
  if (rows.length === 0) {
    return (
      <div data-testid="bi-actions-empty" className="text-xs text-slate-400">
        No action has been proposed. Propose one from an actionable cause above.
      </div>
    );
  }
  return (
    <ul data-testid="bi-actions-list" className="space-y-2">
      {rows.map((action) => (
        <li key={action.action_id} data-testid={`bi-action-${action.action_id}`}
          className="rounded-lg border border-slate-800 bg-slate-950 p-3 space-y-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-xs font-semibold text-slate-100">
                {monthLabel(action.month)} · {action.driver || "unattributed"} · {action.series}
              </div>
              <div className="text-[11px] text-slate-400">
                Routed to {action.approver} · proposed by {action.actor}
              </div>
            </div>
            <span data-testid={`bi-action-state-${action.action_id}`}
              className={`px-2 py-0.5 rounded border text-[10px] font-semibold ${STATE_STYLES[action.state] || STATE_STYLES.pending}`}>
              {action.state}
            </span>
          </div>
          <p className="text-[11px] text-slate-300">{action.note}</p>
          {action.state === "pending" && canWrite && (
            <div className="flex gap-2">
              <button data-testid={`bi-action-approve-${action.action_id}`} disabled={busy}
                onClick={() => onDecide(action, "approved")}
                className="px-2 py-1 rounded border border-emerald-800 bg-emerald-950 text-emerald-200 text-[11px] font-semibold disabled:opacity-50 flex items-center gap-1">
                <Check className="h-3 w-3" /> Approve
              </button>
              <button data-testid={`bi-action-reject-${action.action_id}`} disabled={busy}
                onClick={() => onDecide(action, "rejected")}
                className="px-2 py-1 rounded border border-rose-800 bg-rose-950 text-rose-200 text-[11px] font-semibold disabled:opacity-50 flex items-center gap-1">
                <X className="h-3 w-3" /> Reject
              </button>
            </div>
          )}
          {action.state === "pending" && !canWrite && (
            <p className="text-[11px] text-slate-400">Read-only role: only a planner or admin can decide.</p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function AnnotationForm({ annotations, canWrite, onAnnotate, busy }) {
  const [targetType, setTargetType] = useState("item");
  const [targetId, setTargetId] = useState("");
  const [body, setBody] = useState("");
  const rows = annotations || [];

  const submit = (event) => {
    event.preventDefault();
    if (!targetId.trim() || !body.trim()) return;
    onAnnotate({ target_type: targetType, target_id: targetId.trim(), body: body.trim() });
    setBody("");
  };

  return (
    <div className="space-y-3">
      {canWrite ? (
        <form onSubmit={submit} className="space-y-2" data-testid="bi-annotate-form">
          <div className="flex flex-wrap gap-2">
            <label className="text-[11px] text-slate-400 flex items-center gap-1">
              Target
              <select data-testid="bi-annotate-target-type" value={targetType} aria-label="Annotation target type"
                onChange={(e) => setTargetType(e.target.value)}
                className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-100">
                {TARGETS.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <input data-testid="bi-annotate-target-id" value={targetId} aria-label="Annotation target id"
              onChange={(e) => setTargetId(e.target.value)} placeholder="e.g. 100001 or amazon"
              className="flex-1 min-w-[8rem] rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-100 placeholder:text-slate-500" />
          </div>
          <textarea data-testid="bi-annotate-body" value={body} aria-label="Annotation body" rows={2}
            onChange={(e) => setBody(e.target.value)} placeholder="What did you find?"
            className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-100 placeholder:text-slate-500" />
          <button data-testid="bi-annotate-submit" type="submit" disabled={busy || !targetId.trim() || !body.trim()}
            className="px-3 py-1.5 rounded border border-indigo-700 bg-indigo-950 text-indigo-200 text-[11px] font-semibold disabled:opacity-50 flex items-center gap-1">
            <MessageSquare className="h-3 w-3" /> {busy ? "Saving..." : "Annotate"}
          </button>
        </form>
      ) : (
        <p className="text-[11px] text-slate-400">Read-only role: annotations require a planner or admin.</p>
      )}

      {rows.length === 0 ? (
        <div data-testid="bi-annotations-empty" className="text-xs text-slate-400">No annotations yet.</div>
      ) : (
        <ul data-testid="bi-annotations-list" className="space-y-1.5">
          {rows.map((note) => (
            <li key={note.id} className="text-[11px] text-slate-300 border-t border-slate-800 pt-1.5">
              <span className="font-mono text-slate-400">{note.target_type}:{note.target_id}</span>{" "}
              {note.body}
              <span className="text-slate-500"> — {note.author}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
