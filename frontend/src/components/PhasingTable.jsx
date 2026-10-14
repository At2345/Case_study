import React from "react";
import { CalendarClock } from "lucide-react";
import { fmtNum, monthLabel } from "../lib/format";

export default function PhasingTable({ phasing, months = 4 }) {
  const rows = (phasing || []).slice(0, months);
  if (rows.length === 0) return null;
  const anyBook = rows.some((r) => r.booked > 0 || r.carry_in > 0 || r.shifted_in > 0);
  return (
    <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 text-xs" data-testid="phasing-table">
      <div className="font-semibold text-slate-300 text-[11px] uppercase tracking-wider flex items-center gap-1.5 mb-2">
        <CalendarClock className="h-3.5 w-3.5 text-indigo-400" /> Order-book phasing (demand month vs delivery month)
      </div>
      <table className="w-full text-right font-mono">
        <thead className="text-slate-500 font-sans">
          <tr>
            <th className="text-left font-normal pb-1">Month</th>
            <th className="font-normal pb-1">Demand plan</th>
            <th className="font-normal pb-1">Expected delivery</th>
            {anyBook && <th className="font-normal pb-1">Booked</th>}
            {anyBook && <th className="font-normal pb-1">Moves out</th>}
            {anyBook && <th className="font-normal pb-1">Moves in</th>}
          </tr>
        </thead>
        <tbody className="text-slate-300">
          {rows.map((r) => (
            <tr key={r.month}>
              <td className="text-left font-sans text-slate-400">{monthLabel(r.month)}</td>
              <td>{fmtNum(r.demand)}</td>
              <td className="text-white">{fmtNum(r.expected_delivery)}</td>
              {anyBook && <td className={r.order_book_floor ? "text-amber-300" : ""}>{fmtNum(r.booked)}</td>}
              {anyBook && <td>{r.booked_shifted ? fmtNum(r.booked_shifted) : "-"}</td>}
              {anyBook && <td>{r.carry_in + r.shifted_in ? fmtNum(r.carry_in + r.shifted_in) : "-"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[10px] text-slate-500 mt-2">
        Demand is booked in the month the material is available; a share is delivered (and billed) the following month.
        Booked, moves out and moves in come from the order book as it stands today.
      </p>
    </div>
  );
}
