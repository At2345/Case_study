import React from "react";
import { AlertOctagon } from "lucide-react";
import { monthLabel } from "../lib/format";

export default function QualityBanner({ warnings }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <div role="alert" className="mx-8 mt-4 bg-amber-950/60 border border-amber-800 text-amber-200 rounded-lg px-4 py-3 text-xs space-y-1">
      {warnings.map((w) => (
        <div key={`${w.channel}-${w.month}`} className="flex items-start gap-2">
          <AlertOctagon className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            <strong>Incomplete actuals:</strong> {w.channel} for {monthLabel(w.month)} is {(w.imputed_share * 100).toFixed(0)}% imputed
            across {w.items.toLocaleString("en-GB")} items. The month is excluded from the statistical forecasts and baseline, not treated as zero demand.
          </span>
        </div>
      ))}
    </div>
  );
}
