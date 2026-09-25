import { useRef, useState } from "react";
import { budgetContinuationPreview, continueBudget, type BudgetContinuationPreview } from "./api/client";
import { CostOverrideControls, type CostOverride } from "./CostOverrideControls";

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });

export function BudgetContinuation({ episodeId, onRestored }: { episodeId: string; onRestored: (newEpisodeId: string) => void }) {
  const [preview, setPreview] = useState<BudgetContinuationPreview | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState<CostOverride>(null);
  const [authorizePaid, setAuthorizePaid] = useState(false);
  const [acceptUpdate, setAcceptUpdate] = useState(false);
  const submitLock = useRef(false);

  async function inspect() {
    setOpen(true);
    setLoading(true);
    setError("");
    setPreview(null);
    setSelection(null);
    setAuthorizePaid(false);
    setAcceptUpdate(false);
    try {
      setPreview(await budgetContinuationPreview(episodeId));
    } catch (caught) {
      setError(String(caught));
    } finally {
      setLoading(false);
    }
  }

  async function submit() {
    const plan = preview?.plan;
    if (!preview?.available || !plan || selection === null || !authorizePaid ||
        (plan.source_compatibility === "compatible_update" && !acceptUpdate) ||
        error || loading || submitting || submitLock.current) return;
    submitLock.current = true;
    setSubmitting(true);
    setError("");
    try {
      const common = {
        parent_terminal_hash: plan.parent_terminal_hash,
        plan_hash: plan.plan_hash,
        authorize_paid: true as const,
        accept_compatible_update: plan.source_compatibility === "compatible_update" && acceptUpdate,
      };
      const input = selection === 10
        ? { ...common, additional_cost_usd: 10 as const }
        : { ...common, combined_cap_usd: "uncapped" as const, confirm_uncapped: true as const };
      const result = await continueBudget(episodeId, input);
      onRestored(result.episode_id);
    } catch (caught) {
      setError(`${String(caught)} Refresh the plan and review it again before retrying.`);
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  const plan = preview?.plan;
  const canSubmit = Boolean(
    preview?.available && plan && selection !== null && authorizePaid &&
    (plan.source_compatibility !== "compatible_update" || acceptUpdate) &&
    !error && !loading && !submitting,
  );

  return <section className="panel" aria-label="Budget continuation">
    <div className="area-title">
      <div><h2>Continue after budget stop</h2><p className="muted">Creates a separate, unscored child. The original run stays unchanged.</p></div>
      {!open && <button onClick={() => void inspect()}>Review cost override</button>}
    </div>
    {open && <div>
      {loading && <p role="status">Reviewing the continuation plan…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {preview && !preview.available && <p role="status">Continuation unavailable: {preview.reason || "The server did not provide a reason."}</p>}
      {preview && error && <button disabled={submitting} onClick={() => void inspect()}>Refresh plan</button>}
      {preview?.available && plan && <div>
        <h3>Review additional spend</h3>
        <p>Recorded all-attempt spend: {dollars.format(plan.accounted_usd)}.</p>
        <p>A $10 continuation allowance makes the new total ceiling {dollars.format(plan.new_cap_usd)}.</p>
        <p>This launches 0 preliminary tests and 1 checked restore in the same game instance. The continuation is a separate, unscored child; the original run and research record are unchanged.</p>
        {plan.source_compatibility === "compatible_update" && <p><strong>Compatible code update:</strong> this continuation uses compatible updated code. Historical protocol identity is preserved.</p>}
        <CostOverrideControls
          value={selection}
          onChange={setSelection}
          disabled={loading || submitting}
          showCurrent={false}
          tenDollarLabel="$10 more"
          legend="Additional spend for this continuation"
          description="The allowance applies to additional spend on this stopped run. Uncapped has no dollar ceiling; call and action limits still apply."
        />
        <label><input type="checkbox" checked={authorizePaid} disabled={submitting} onChange={(event) => setAuthorizePaid(event.target.checked)} /> I authorize the paid continuation shown by this plan.</label>
        {plan.source_compatibility === "compatible_update" && <label><input type="checkbox" checked={acceptUpdate} disabled={submitting} onChange={(event) => setAcceptUpdate(event.target.checked)} /> I accept continuing with this compatible code update.</label>}
        <div className="actions">
          <button
            className={selection === "uncapped" ? "cost-override-uncapped" : "primary"}
            style={selection === "uncapped" ? { color: "#fff", backgroundColor: "#8e2932", borderColor: "#d45b65" } : undefined}
            disabled={!canSubmit}
            onClick={() => void submit()}
          >
            {submitting ? "Continuing…" : selection === "uncapped" ? "Continue uncapped" : "Add $10 and continue"}
          </button>
        </div>
      </div>}
      {preview && !preview.available && !error && <button disabled={submitting} onClick={() => void inspect()}>Refresh plan</button>}
      {error && !preview && <button disabled={submitting} onClick={() => void inspect()}>Retry plan</button>}
      <button disabled={submitting} onClick={() => { setOpen(false); setPreview(null); setError(""); setSelection(null); setAuthorizePaid(false); setAcceptUpdate(false); }}>Cancel</button>
    </div>}
  </section>;
}
