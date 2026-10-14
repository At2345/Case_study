import React, { useCallback, useEffect, useRef, useState } from "react";
import { Bell, CheckCheck, AlertOctagon, ListChecks } from "lucide-react";
import { api } from "../services/api";

const POLL_MS = 30000;

export default function NotificationBell({ onNavigate }) {
  const [data, setData] = useState({ unread: 0, items: [] });
  const [open, setOpen] = useState(false);
  const box = useRef(null);

  const load = useCallback(() => api.notifications().then(setData).catch(() => {}), []);
  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => {
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const openNote = async (n) => {
    if (!n.read) await api.readNotification(n.id).catch(() => {});
    setOpen(false);
    if (n.link) onNavigate(n.link);
    load();
  };
  const readAll = async () => { await api.readAllNotifications().catch(() => {}); load(); };

  return (
    <div className="relative" ref={box}>
      <button data-testid="notification-bell" onClick={() => setOpen((o) => !o)} aria-label="Notifications"
        className="relative p-2 rounded-lg bg-slate-800/80 border border-slate-700 hover:bg-slate-700 transition-colors">
        <Bell className="h-4 w-4 text-slate-200" />
        {data.unread > 0 && (
          <span data-testid="notification-unread-count"
            className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-amber-500 text-[10px] font-bold text-slate-950 flex items-center justify-center">
            {data.unread}
          </span>
        )}
      </button>
      {open && (
        <div data-testid="notification-panel" className="absolute right-0 mt-2 w-96 bg-slate-900 border border-slate-700 rounded-xl shadow-2xl z-[70]">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
            <span className="text-xs font-bold text-white">Notifications</span>
            <button data-testid="notification-read-all-btn" onClick={readAll} disabled={data.unread === 0}
              className="text-[11px] text-indigo-300 hover:text-indigo-200 disabled:opacity-40 flex items-center gap-1">
              <CheckCheck className="h-3.5 w-3.5" /> Mark all read
            </button>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {data.items.length === 0 && <li className="px-4 py-6 text-xs text-slate-500 text-center">No notifications yet.</li>}
            {data.items.map((n) => (
              <li key={n.id}>
                <button data-testid={`notification-item-${n.id}`} onClick={() => openNote(n)}
                  className={`w-full text-left px-4 py-3 border-b border-slate-800/60 hover:bg-slate-800/50 flex gap-3 ${n.read ? "opacity-60" : ""}`}>
                  {n.kind === "error" ? <AlertOctagon className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" /> : <ListChecks className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />}
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-white">{n.title}</span>
                      {!n.read && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />}
                    </span>
                    <span className="block text-[11px] text-slate-400 mt-0.5 leading-relaxed">{n.body}</span>
                    <span className="block text-[10px] text-slate-500 mt-1">{new Date(n.created_at).toLocaleString("en-GB")}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
