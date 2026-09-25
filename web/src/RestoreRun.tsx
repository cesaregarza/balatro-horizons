import { useRef, useState } from "react";
import { restorePreview, restoreRun, type RestorePreview } from "./api/client";

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });
function amount(value: number | "uncapped" | null) {
  if (value === "uncapped") return <strong style={{ color: "#f07882" }}>Uncapped</strong>;
  return value == null ? "No cap" : dollars.format(value);
}

export function RestoreRun({ episodeId, onRestored }: { episodeId: string; onRestored: (newEpisodeId: string) => void }) {
  const [preview, setPreview] = useState<RestorePreview | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [authorizePaid, setAuthorizePaid] = useState(false);
  const [acceptUpdate, setAcceptUpdate] = useState(false);
  const submitLock = useRef(false);

  async function inspect() {
    setOpen(true); setLoading(true); setError(""); setPreview(null);
    setAuthorizePaid(false); setAcceptUpdate(false);
    try { setPreview(await restorePreview(episodeId)); }
    catch (caught) { setError(String(caught)); }
    finally { setLoading(false); }
  }
  async function confirm() {
    if (!preview?.available || !preview.plan || submitLock.current) return;
    const { max_episode_cost_usd: episodeLimit, max_batch_cost_usd: batchLimit } = preview.plan.limits;
    const uncappedLimits = [
      episodeLimit === "uncapped" ? "episode" : null,
      batchLimit === "uncapped" ? "campaign" : null,
    ].filter((limit): limit is string => limit !== null);
    const finiteLimits = [
      typeof episodeLimit === "number" ? `episode ceiling ${amount(episodeLimit)}` : null,
      typeof batchLimit === "number" ? `campaign ceiling ${amount(batchLimit)}` : null,
    ].filter((limit): limit is string => limit !== null);
    const confirmUncapped = uncappedLimits.length > 0;
    const uncappedDescription = uncappedLimits.length > 1 ? "ceilings are uncapped" : "ceiling is uncapped";
    const warning = finiteLimits.length > 0
      ? `Are you sure? The ${uncappedLimits.join(" and ")} ${uncappedDescription}, but the ${finiteLimits.join(" and ")} still applies. Call and action limits still apply.`
      : "Are you sure? This continuation has no dollar ceiling. Spending can keep growing. Call and action limits still apply.";
    if (confirmUncapped && !window.confirm(warning)) return;
    submitLock.current = true;
    setSubmitting(true); setError("");
    try {
      const result = await restoreRun(episodeId, {
        parent_head: preview.plan.parent_head,
        plan_hash: preview.plan.plan_hash,
        authorize_paid: preview.plan.requires_paid_authorization && authorizePaid,
        accept_compatible_update: preview.plan.source_compatibility === "compatible_update" && acceptUpdate,
        ...(confirmUncapped ? { confirm_uncapped: true } : {}),
      });
      onRestored(result.episode_id);
    } catch (caught) {
      setError(`${String(caught)} Refresh the plan and review it again before retrying.`);
    } finally { submitLock.current = false; setSubmitting(false); }
  }

  const plan = preview?.plan;
  const needsPaid = Boolean(plan?.requires_paid_authorization);
  const needsUpdate = plan?.source_compatibility === "compatible_update";
  const canConfirm = Boolean(plan && !error && (!needsPaid || authorizePaid) && (!needsUpdate || acceptUpdate) && !submitting);
  return <section className="panel" aria-label="Restore run">
    <div className="area-title"><div><h2>Continue from a checkpoint</h2><p className="muted">Creates a separate, unscored continuation. The original run stays immutable.</p></div>
      {!open && <button onClick={() => void inspect()}>Restore run</button>}
    </div>
    {open && <div>
      {loading && <p role="status">Checking the latest restore plan…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {preview && !preview.available && <p role="status">Restore unavailable: {preview.reason || "The server did not provide a reason."}</p>}
      {preview && error && <button onClick={() => void inspect()}>Refresh plan</button>}
      {preview?.available && plan && <div>
        <h3>Review this continuation</h3>
        <p>Latest checkpoint: resume before decision {plan.decision + 1}.</p>
        <p>Spend so far: {dollars.format(plan.costs.accounted_usd)}. Episode cap: {amount(plan.limits.max_episode_cost_usd)} total; {amount(plan.costs.remaining_episode_usd)} remaining. Batch cap: {amount(plan.limits.max_batch_cost_usd)} total; {amount(plan.costs.remaining_batch_usd)} remaining.</p>
        <p>Launches: 0 preliminary checks and 1 checked restore, in the same game instance.</p>
        <p>The continuation is separate and unscored; the original episode and its research record are preserved.</p>
        {needsUpdate && <p><strong>Compatible code update:</strong> this continuation uses compatible updated code. Historical protocol identity is preserved.</p>}
        {needsPaid && <label><input type="checkbox" checked={authorizePaid} onChange={(event) => setAuthorizePaid(event.target.checked)} /> I authorize the paid continuation shown by this plan.</label>}
        {needsUpdate && <label><input type="checkbox" checked={acceptUpdate} onChange={(event) => setAcceptUpdate(event.target.checked)} /> I accept continuing with this compatible code update.</label>}
        <div className="actions"><button className="primary" disabled={!canConfirm} onClick={() => void confirm()}>{submitting ? "Restoring…" : "Restore and continue"}</button></div>
      </div>}
      {preview && !preview.available && !error && <button onClick={() => void inspect()}>Refresh plan</button>}
      {error && !preview && <button onClick={() => void inspect()}>Retry plan</button>}
      <button disabled={submitting} onClick={() => { setOpen(false); setPreview(null); setError(""); }}>Cancel</button>
    </div>}
  </section>;
}
