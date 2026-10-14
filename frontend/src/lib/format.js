export const fmtNum = (v, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "-"
    : Number(v).toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const fmtGbp = (v) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  const sign = v < 0 ? "-" : v > 0 ? "+" : "";
  return `${sign}\u00a3${Math.abs(Number(v)).toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;
};

export const fmtGbpPlain = (v) =>
  v === null || v === undefined ? "-" : `\u00a3${Number(v).toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export const fmtPct = (v, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export const monthLabel = (iso) =>
  new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });

export const RISK_LABELS = {
  OOS_RISK: "OOS exposure",
  OVERSTOCK_RISK: "Overstock risk",
  CRITICAL_AMAZON_BUYBOX_ALERT: "Buy Box alert",
  TERMINAL_OBSOLESCENCE_RISK: "Terminal obsolescence",
  NPI_ANALOGY_GAP: "NPI analogy gap",
};

export const RISK_STYLES = {
  OOS_RISK: "bg-rose-950 text-rose-300 border-rose-800",
  CRITICAL_AMAZON_BUYBOX_ALERT: "bg-red-900 text-red-200 border-red-700",
  OVERSTOCK_RISK: "bg-amber-950 text-amber-300 border-amber-800",
  TERMINAL_OBSOLESCENCE_RISK: "bg-orange-950 text-orange-300 border-orange-800",
  NPI_ANALOGY_GAP: "bg-sky-950 text-sky-300 border-sky-800",
};
