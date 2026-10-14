import React, { useEffect, useState } from "react";
import { Clock, Save } from "lucide-react";
import { api } from "../services/api";
import { Panel, ErrorNote } from "./ui";

const LABEL = { monthly_cycle: "Monthly cycle", amazon_daily: "Amazon daily pipeline" };
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { timeZone: "Europe/London", dateStyle: "medium", timeStyle: "short" }) : "-");

function ScheduleRow({ s, isAdmin, onSaved }) {
  const [form, setForm] = useState({ enabled: s.enabled, frequency: s.frequency, day: s.day || 1, hour: s.hour, minute: s.minute });
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const save = async () => {
    setError(null);
    setSaved(false);
    try {
      await api.updateSchedule(s.job, { enabled: form.enabled, frequency: form.frequency, hour: Number(form.hour), minute: Number(form.minute),
        day: form.frequency === "monthly" ? Number(form.day) : null });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e.message);
    }
  };

  const input = "bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-200 disabled:opacity-60";
  return (
    <div data-testid={`schedule-${s.job}`} className="bg-slate-950/60 border border-slate-800 rounded-lg p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <Clock className="h-4 w-4 text-indigo-400" />
        <span className="font-semibold text-white">{LABEL[s.job] || s.job}</span>
        <span className="text-slate-400">Next run: <span data-testid={`schedule-next-${s.job}`} className="text-slate-200">{s.enabled ? fmtTime(s.next_run_at) : "paused"}</span></span>
        {s.last_run_at && <span className="text-slate-500">Last: {fmtTime(s.last_run_at)} ({s.last_outcome})</span>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <label className="flex items-center gap-1.5">
          <input data-testid={`schedule-enabled-${s.job}`} type="checkbox" checked={form.enabled} onChange={set("enabled")} disabled={!isAdmin} /> Enabled
        </label>
        <select data-testid={`schedule-frequency-${s.job}`} value={form.frequency} onChange={set("frequency")} disabled={!isAdmin} className={input}>
          <option value="daily">Daily</option><option value="monthly">Monthly</option>
        </select>
        {form.frequency === "monthly" && (
          <label className="flex items-center gap-1">on day
            <input data-testid={`schedule-day-${s.job}`} type="number" min="1" max="28" value={form.day} onChange={set("day")} disabled={!isAdmin} className={`${input} w-14`} />
          </label>
        )}
        <label className="flex items-center gap-1">at
          <input data-testid={`schedule-hour-${s.job}`} type="number" min="0" max="23" value={form.hour} onChange={set("hour")} disabled={!isAdmin} className={`${input} w-14`} />:
          <input data-testid={`schedule-minute-${s.job}`} type="number" min="0" max="59" value={form.minute} onChange={set("minute")} disabled={!isAdmin} className={`${input} w-14`} />
        </label>
        <span>UK time</span>
        {isAdmin && (
          <button data-testid={`schedule-save-${s.job}`} onClick={save}
            className="ml-auto px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-xs font-semibold text-white flex items-center gap-1.5">
            <Save className="h-3.5 w-3.5" /> Save
          </button>
        )}
        {saved && <span className="text-emerald-400">Saved</span>}
      </div>
      <ErrorNote error={error} />
    </div>
  );
}

export default function SchedulePanel({ isAdmin }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = () => api.schedules().then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  return (
    <Panel title="Run schedules" testId="schedules-panel"
      subtitle="Runs start automatically in UK time. When the monthly cycle reaches human review, planners get an in-app notification that the exception queue is ready; failures notify admins.">
      <ErrorNote error={error} />
      <div className="space-y-3">
        {data && data.schedules.map((s) => <ScheduleRow key={s.job + s.next_run_at} s={s} isAdmin={isAdmin} onSaved={load} />)}
      </div>
    </Panel>
  );
}
