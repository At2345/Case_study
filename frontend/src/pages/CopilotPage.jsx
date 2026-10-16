import React, { useEffect, useRef, useState } from "react";
import { Send, Trash2, Sparkles, RefreshCw, ArrowRight, Eye } from "lucide-react";
import { api } from "../services/api";
import { ErrorNote } from "../components/ui";
import { fmtGbp, fmtNum } from "../lib/format";

const SUGGESTIONS = [
  "Which three exceptions carry the most value at risk, and why?",
  "How big is the OTIF exposure and which items drive it?",
  "Summarise the data-quality issues in this cycle.",
  "How do B2B demand and expected deliveries differ next month?",
  "How does the monthly planning cycle work?",
  "What is Croston's method in demand forecasting?",
];

const TIER_LABELS = { project: "project data", general_knowledge: "general knowledge", none: "not in project data" };
const TIER_STYLES = {
  project: "bg-indigo-950 text-indigo-300",
  general_knowledge: "bg-amber-950 text-amber-300",
  none: "bg-slate-800 text-slate-400",
};

function ActionChips({ actions, onNavigate }) {
  if (!actions || actions.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5" data-testid="copilot-actions">
      {actions.map((a, i) => (
        <button key={`${a.type}-${i}`} type="button" data-testid={`copilot-action-${i}`} onClick={() => onNavigate(a)}
          className="flex items-center gap-1 px-2 py-1 bg-indigo-950/60 hover:bg-indigo-900/60 border border-indigo-800 rounded-md text-[10px] font-semibold text-indigo-200">
          <ArrowRight className="h-3 w-3" /> {a.title}
        </button>
      ))}
    </div>
  );
}

function DetailView({ action, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    const load = action.item_id ? api.item(action.item_id) : api.runDetail(action.run_id);
    load.then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [action.item_id, action.run_id]);
  return (
    <div className="mt-2 rounded-lg border border-indigo-900 bg-indigo-950/30 p-3 text-[11px]" data-testid="copilot-detail">
      <div className="flex items-center justify-between mb-1.5">
        <span className="font-semibold text-indigo-200">{action.title}</span>
        <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">Close</button>
      </div>
      {error && <div className="text-rose-300">{error}</div>}
      {!data && !error && <div className="text-slate-400">Loading details...</div>}
      {data && action.item_id && (
        <div className="space-y-1 text-slate-300">
          <div>Item {data.item?.item_id} · {data.item?.channel} · {data.item?.phase} · {data.item?.state}</div>
          <div>Risk: {data.item?.risk_type} · value delta {fmtGbp(data.item?.value_delta_gbp)}</div>
          <div>Plan M1: baseline {fmtNum(data.plan?.[0]?.baseline)}, ML {fmtNum(data.plan?.[0]?.ml)}, consensus {fmtNum(data.plan?.[0]?.consensus)}</div>
        </div>
      )}
      {data && action.run_id && (
        <div className="space-y-1 text-slate-300">
          <div>Status <strong className="text-slate-100">{data.status}</strong> · started {new Date(data.started_at).toLocaleString("en-GB")} by {data.started_by}</div>
          <div className="flex flex-wrap gap-1">
            {(data.steps || []).map((s, i) => (
              <span key={`${s.node}-${i}`} className={`px-1.5 py-0.5 rounded text-[9px] font-semibold ${s.status === "failed" ? "bg-rose-950 text-rose-300" : "bg-slate-800 text-emerald-300"}`}>
                {s.node.replace(/_/g, " ")} {s.seconds}s
              </span>
            ))}
          </div>
          {data.error && <div className="text-rose-300">{data.error}</div>}
        </div>
      )}
    </div>
  );
}

export default function CopilotPage({ onNavigateAction }) {
  const [messages, setMessages] = useState([]);
  const [enabled, setEnabled] = useState(true);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [detailAction, setDetailAction] = useState(null);
  const end = useRef(null);

  useEffect(() => {
    api.assistantHistory().then((h) => { setMessages((prev) => (prev.length ? prev : h.messages)); setEnabled(h.enabled); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { if (end.current) end.current.scrollIntoView({ block: "end" }); }, [messages]);

  const patchLast = (patch) => setMessages((m) => [...m.slice(0, -1), { ...m[m.length - 1], ...patch }]);

  const ask = async (q) => {
    const question = (q || input).trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { role: "user", text: question }, { role: "assistant", text: "", actions: [], tier: null, sources: [] }]);
    try {
      await api.assistantStream(question,
        (delta) => setMessages((m) => [...m.slice(0, -1), { ...m[m.length - 1], text: m[m.length - 1].text + delta }]),
        (actions) => patchLast({ actions }),
        (meta) => patchLast({ tier: meta.tier || null, sources: meta.sources || [] }));
    } catch (e) {
      setError(e.message);
      setMessages((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    try { await api.assistantClear(); setMessages([]); } catch (e) { setError(e.message); }
  };

  return (
    <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-12 gap-6" data-testid="copilot-page">
      <aside className="lg:col-span-4 space-y-3">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <h2 className="text-sm font-bold text-white flex items-center gap-2"><Sparkles className="h-4 w-4 text-indigo-400" /> S&amp;OP co-pilot</h2>
          <p className="text-[11px] text-slate-400 mt-2 leading-relaxed">
            Ask about the current cycle. Answers use the live cycle data only: the queue,
            order-book phasing, OTIF exposure, safety stock and data-quality warnings. Check figures against the workbench before acting.
          </p>
        </div>
        <div className="space-y-2">
          {SUGGESTIONS.map((s, i) => (
            <button key={s} data-testid={`copilot-suggestion-${i}`} onClick={() => ask(s)} disabled={busy || !enabled}
              className="w-full text-left text-xs text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-lg px-3 py-2 disabled:opacity-50">
              {s}
            </button>
          ))}
        </div>
      </aside>
      <section className="lg:col-span-8 bg-slate-900 border border-slate-800 rounded-xl flex flex-col h-[70vh]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800">
          <span className="text-xs text-slate-400">{messages.length} messages, saved per user</span>
          <button data-testid="copilot-clear-btn" onClick={clear} className="text-xs text-slate-400 hover:text-white flex items-center gap-1">
            <Trash2 className="h-3.5 w-3.5" /> Clear
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4" data-testid="copilot-messages">
          {messages.length === 0 && <div className="text-xs text-slate-500 text-center mt-20">No questions yet. Pick a suggestion or type below.</div>}
          {messages.map((m, i) => (
            <div key={m.id || i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div data-testid={`copilot-msg-${m.role}`}
                className={`max-w-[85%] whitespace-pre-wrap text-xs leading-relaxed rounded-xl px-4 py-3 ${m.role === "user" ? "bg-indigo-600 text-white" : "bg-slate-950 border border-slate-800 text-slate-200"}`}>
                {(m.text || "").replace(/\*\*/g, "") || (busy && i === messages.length - 1 ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : "")}
                {m.role === "assistant" && m.tier && (
                  <span data-testid={`copilot-tier-${m.tier}`}
                    className={`inline-block mt-1.5 px-1.5 py-0.5 rounded text-[9px] font-semibold ${TIER_STYLES[m.tier] || ""}`}>
                    {TIER_LABELS[m.tier] || m.tier}
                  </span>
                )}
                {m.role === "assistant" && m.sources && m.sources.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {m.sources.map((s, i) => (
                      <span key={`${s.title}-${i}`} title={s.snippet} data-testid={`copilot-source-${i}`}
                        className="px-1.5 py-0.5 bg-slate-800 rounded text-[9px] text-slate-400">
                        {s.title}
                      </span>
                    ))}
                  </div>
                )}
                {m.role === "assistant" && onNavigateAction && <ActionChips actions={m.actions} onNavigate={onNavigateAction} />}
                {m.role === "assistant" && (m.actions || []).some((a) => a.item_id || a.run_id) && (
                  <div className="mt-2">
                    <button type="button" data-testid={`copilot-details-btn-${i}`}
                      onClick={() => setDetailAction(detailAction === m ? null : m)}
                      className="flex items-center gap-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md text-[10px] font-semibold text-slate-200">
                      <Eye className="h-3 w-3" /> View details
                    </button>
                    {detailAction === m && (() => {
                      const a = (m.actions || []).find((x) => x.item_id || x.run_id);
                      return a ? <DetailView action={a} onClose={() => setDetailAction(null)} /> : null;
                    })()}
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={end} />
        </div>
        <div className="p-4 border-t border-slate-800 space-y-2">
          <ErrorNote error={error} />
          {!enabled && <div className="text-xs text-amber-300">The planning assistant is not configured on the server.</div>}
          <form onSubmit={(e) => { e.preventDefault(); ask(); }} className="flex gap-2">
            <input data-testid="copilot-input" value={input} onChange={(e) => setInput(e.target.value)} maxLength={1000}
              placeholder="Ask about the exception queue, OTIF, phasing, safety stock..."
              className="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500" />
            <button data-testid="copilot-send-btn" type="submit" disabled={busy || !input.trim() || !enabled}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 rounded-lg text-xs font-semibold flex items-center gap-2">
              <Send className="h-4 w-4" /> Ask
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
