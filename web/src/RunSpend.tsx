import type { RunSpendTotals } from "./api/client";
import "./runSpend.css";

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4,
});

function money(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || value < 0) return "Unavailable";
  return value > 0 && value < 0.0001 ? "<$0.0001" : dollars.format(value);
}

export function RunSpend({ spend, fallbackCost, loading = false, status, compact = false }: {
  spend?: RunSpendTotals;
  fallbackCost?: number;
  loading?: boolean;
  status: string;
  compact?: boolean;
}) {
  const splitKnown = spend?.response_usd != null && spend.reserved_usd != null;
  return <section className={`run-spend${compact ? " compact" : ""}`} aria-label="Run API spend">
    <div>
      <h2>API spend <span>(USD)</span></h2>
      <strong className="run-spend-total" aria-live="polite">{loading ? "Loading…" : money(spend ? spend.accounted_usd : fallbackCost)}</strong>
      <p className="run-spend-status">{status}</p>
    </div>
    <div className="run-spend-accounting">
      <p>Harness-accounted total for this episode, not a provider invoice or in-game cash.</p>
      {!loading && (splitKnown ? <p><b>{money(spend.response_usd)}</b> recorded responses · <b>{money(spend.reserved_usd)}</b> reserved for pending / unknown usage</p> : <p>Reservation breakdown unavailable.</p>)}
      <small>Response costs may retain a conservative estimate when usage is unavailable.</small>
    </div>
  </section>;
}
