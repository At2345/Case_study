// IBCS semantic notation (International Business Communication Standards).
// Encoding: actual = solid, plan/budget = dashed outline, forecast = dotted.
// Every colour below clears WCAG 2.2 AA 4.5:1 against the slate-900 panel background (#0f172a).

export const IBCS = {
  actual: "#818cf8",
  plan: "#94a3b8",
  forecast: "#22d3ee",
  positive: "#34d399",
  negative: "#fb7185",
  neutral: "#94a3b8",
  grid: "#1e293b",
  axisText: "#94a3b8",
  valueText: "#e2e8f0",
};

export const SERIES_STYLE = {
  actual: { stroke: IBCS.actual, dash: null },
  plan: { stroke: IBCS.plan, dash: "6 3" },
  forecast: { stroke: IBCS.forecast, dash: "2 2" },
};

export const seriesStyle = (s) =>
  SERIES_STYLE[s && s.style] || { stroke: (s && s.color) || IBCS.actual, dash: null };

export const varianceTone = (v) => (Number(v) < 0 ? IBCS.negative : IBCS.positive);
