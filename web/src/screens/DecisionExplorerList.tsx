import type { DecisionRow } from "../api/client";
import { actionResult, actionTitle, jokerChanges } from "../decisionPresentation";
import { modifierDescription, editionClass } from "../cardPresentation";

export function DecisionExplorerList({
  rows,
  active,
  onChoose,
}: {
  rows: DecisionRow[];
  active: DecisionRow | undefined;
  onChoose: (row: DecisionRow) => void;
}) {
  return <section className="decision-list" aria-label="Recorded choices">
    {rows.map((row, index) => <div key={row.event_id}>
      {(index === 0 || rows[index - 1].ante !== row.ante) && <h2 className="decision-group">Ante {row.ante ?? "unknown"}</h2>}
      <button id={`choice-${row.event_id}`} className={`decision-row ${row.event_id === active?.event_id ? "selected" : ""}`} aria-current={row.event_id === active?.event_id ? "true" : undefined} onClick={() => onChoose(row)}>
        <span className="decision-number">#{row.decision + 1}</span>
        <span className="decision-row-body">
          <strong className={`card-name ${editionClass(row.effects)}`} title={modifierDescription(row.effects)}>{actionTitle(row)}</strong>
          <span className={!row.action_number ? "rejected" : "decision-result"}>{actionResult(row)}</span>
          {row.note && <span className="decision-note-preview">{row.note}</span>}
          {!!row.jokers_added?.length && <span className="build-change">+ {jokerChanges(row, "added").join(", ")}</span>}
        </span>
        <span aria-hidden="true" className="row-arrow">↗</span>
      </button>
    </div>)}
  </section>;
}
