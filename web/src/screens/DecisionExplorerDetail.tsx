import type { DecisionRow, View } from "../api/client";
import { Board } from "../Board";
import { DevTrace } from "./decision-explorer/DevTrace";
import { ModifierLegend } from "../ModifierLegend";
import { ReasoningSummaries } from "../workbench/ReasoningSummaries";
import { actionResult, actionTitle, humanize, jokerChanges, number } from "../decisionPresentation";
import { modifierDescription, editionClass } from "../cardPresentation";

export function DecisionExplorerDetail({
  token, active, view, activeIndex, visibleLength, detailError, devMode, liveUpdates,
  refresh, boardSide, setBoardSide, busy, onBack, onPrevious, onNext, onAnnotate,
}: {
  token: string;
  active: DecisionRow;
  view: View | null;
  activeIndex: number;
  visibleLength: number;
  detailError: string;
  devMode: boolean;
  liveUpdates: boolean;
  refresh: number;
  boardSide: "before" | "after";
  setBoardSide: (side: "before" | "after") => void;
  busy: boolean;
  onBack: () => void;
  onPrevious: () => void;
  onNext: () => void;
  onAnnotate: () => void;
}) {
  return <section className="decision-detail" aria-label="Decision details">
    <div className="detail-navigation"><button className="back-to-choices" onClick={onBack}>← Back to choices</button><span>{activeIndex + 1} / {visibleLength} matches</span><button aria-label="Previous matching decision" disabled={activeIndex <= 0} onClick={onPrevious}>← Previous</button><button aria-label="Next matching decision" disabled={activeIndex >= visibleLength - 1} onClick={onNext}>Next →</button></div>
    <p className="eyebrow">DECISION {active.decision + 1} · ANTE {active.ante ?? "?"} · {humanize(active.phase)}</p>
    <h2 data-decision-detail-heading tabIndex={-1} title={modifierDescription(active.effects)} className={`card-name ${editionClass(active.effects)}`}>{actionTitle(active)}</h2>
    <p className={!active.action_number ? "rejected" : "detail-result"}>{actionResult(active)}</p>
    {active.rejection_code && <p className="error">{active.rejection_code}</p>}
    {!!active.cards?.length && <p className="choice-cards">{active.cards.join(" · ")}</p>}
    {!!active.targets?.length && <p>Targets: {active.targets.join(" · ")}</p>}
    {!!active.effects?.length && <p>{active.effects.filter(Boolean).join(" · ")}</p>}
    {active.ordering_before && <p>Previous order: {active.ordering_before.join(" → ")}</p>}
    {active.ordered_objects && <p>Requested order: {active.ordered_objects.join(" → ")}</p>}
    <div className="model-note"><small>Model's recorded note</small><blockquote>{active.note || "No decision note recorded."}</blockquote><small>This is the model's stated intention.</small></div>
    {!!active.jokers_added?.length && <p className="build-change">Added: {jokerChanges(active, "added").join(" · ")}</p>}
    {!!active.jokers_removed?.length && <p>Removed: {jokerChanges(active, "removed").join(" · ")}</p>}
    {active.money_after != null && <p className="muted">Cash ${number(active.money_before)} → ${number(active.money_after)}</p>}
    {detailError && <p role="alert" className="error">{detailError}</p>}
    {devMode && <DevTrace key={`${token}-${active.decision}`} token={token} decision={active.decision} liveUpdates={liveUpdates} refresh={refresh} />}
    {!view && !detailError && <p role="status">Loading this decision…</p>}
    {view && <>
      <button className="primary" disabled={busy} onClick={onAnnotate}>Annotate this decision</button>
      <div className="board-toggle" aria-label="State to inspect"><button aria-pressed={boardSide === "before"} onClick={() => setBoardSide("before")}>Before decision</button><button aria-pressed={boardSide === "after"} disabled={!active.action_number || !view.transition} onClick={() => setBoardSide("after")}>After decision</button></div>
      <Board key={`${view.decision}-${boardSide}`} observation={boardSide === "after" && view.transition ? view.transition : view.observation} />
      <ReasoningSummaries events={view.action_events || []} />
      <details><summary>Exact public decision records</summary><pre>{JSON.stringify(view.action_events, null, 2)}</pre></details>
    </>}
  </section>;
}
