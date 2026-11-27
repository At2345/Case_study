import React, { useCallback, useEffect, useState } from "react";
import { Bot, Check, X } from "lucide-react";
import { api } from "../services/api";
import { Panel } from "./ui";

const EXAMPLES = ["Which SKUs are low on stock across my channels?", "Show me today's orders by channel.",
  "Set MAT-BLUE to 0 on eBay, we are out of stock."];

// Ask the local operations agent; its proposed changes wait here for approval.
export default function AgentConsole({ role, onError, onChanged }) {
  const canWrite = role === "planner" || role === "admin";
  const [status, setStatus] = useState(null);
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState(null);
  const [pending, setPending] = useState([]);
  const [busy, setBusy] = useState(false);

  const loadPending = useCallback(() => api.agentProposals("pending").then((r) => setPending(r.proposals))
    .catch((e) => onError(e.message)), [onError]);

  useEffect(() => {
    api.agentStatus().then(setStatus).catch((e) => onError(e.message));
    loadPending();
  }, [loadPending, onError]);

  const ask = async () => {
    setBusy(true);
    try {
      setReply(await api.askAgent(question.trim()));
      loadPending();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const decide = async (p, approved) => {
    try {
      await api.decideAgentProposal(p.id, approved);
      loadPending();
      if (onChanged) onChanged();
    } catch (e) {
      onError(e.message);
    }
  };

  return (
    <Panel title="Operations agent" testId="agent-console"
      subtitle={status && status.available
        ? `Model: ${status.model}${status.local ? " (local, free)" : ""}. It reads your channels and playbooks; changes wait for your approval.`
        : "Answers questions about your channels and proposes changes for approval."}>
      {status && !status.available && (
        <div className="text-xs text-amber-200 bg-amber-950/40 border border-amber-800 rounded p-2 mb-3" data-testid="agent-unavailable">
          {status.hint}
        </div>
      )}
      <textarea data-testid="agent-question" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)}
        aria-label="Ask the agent" placeholder={EXAMPLES[0]}
        className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs" />
      <div className="flex flex-wrap gap-2 mt-2 items-center">
        <button data-testid="agent-ask" onClick={ask} disabled={busy || !status || !status.available || question.trim().length < 3}
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs rounded-lg flex items-center gap-1">
          <Bot className="h-3.5 w-3.5" /> {busy ? "Thinking…" : "Ask"}
        </button>
        {EXAMPLES.map((ex) => (
          <button key={ex} onClick={() => setQuestion(ex)} className="text-[11px] text-slate-400 hover:text-white">{ex}</button>
        ))}
      </div>
      {reply && (
        <div className="mt-3 text-xs space-y-2" data-testid="agent-reply">
          <div className="text-slate-100 whitespace-pre-wrap">{reply.answer}</div>
          {reply.steps.length > 0 && (
            <details className="text-slate-400">
              <summary>{reply.steps.length} step{reply.steps.length === 1 ? "" : "s"}</summary>
              <ol className="list-decimal ml-4 mt-1 space-y-0.5">
                {reply.steps.map((s, i) => <li key={i}>{s.tool ? <code>{s.tool}</code> : s.error}</li>)}
              </ol>
            </details>
          )}
        </div>
      )}
      {pending.length > 0 && (
        <div className="mt-4" data-testid="agent-proposals">
          <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1">Waiting for approval</div>
          <ul className="space-y-2">
            {pending.map((p) => (
              <li key={p.id} className="text-xs bg-slate-950 border border-slate-800 rounded p-2" data-testid={`agent-proposal-${p.id}`}>
                <code className="text-amber-200">{p.tool}</code>{" "}
                <span className="text-slate-300 break-all">{JSON.stringify(p.arguments)}</span>
                <div className="text-[11px] text-slate-500">asked: “{p.question}” · {p.created_by}</div>
                {canWrite && (
                  <div className="flex gap-2 mt-1">
                    <button data-testid={`agent-approve-${p.id}`} onClick={() => decide(p, true)}
                      className="px-2 py-0.5 bg-emerald-700 hover:bg-emerald-600 rounded flex items-center gap-1"><Check className="h-3 w-3" /> Approve</button>
                    <button data-testid={`agent-reject-${p.id}`} onClick={() => decide(p, false)}
                      className="px-2 py-0.5 bg-rose-800 hover:bg-rose-700 rounded flex items-center gap-1"><X className="h-3 w-3" /> Reject</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}
