import type { TimelinePoint } from "../api/client";
import { Trajectory } from "./Trajectory";
import "./comparison.css";

type Run = { episode_id: string; summary: Record<string, unknown> | null; metadata?: Record<string, unknown>; trajectory: TimelinePoint[] };
type Data = { interpretation: string; assistance?: string; branch_decision?: number; runs: Run[] };
const FIELDS: [string, string][] = [["Model", "model_name"], ["Reasoning effort", "reasoning_effort"], ["Harness", "recorded_interface"], ["Context policy", "context_policy"], ["Provider wire policy", "provider_wire_policy"], ["Deck", "deck"], ["Stake", "stake"], ["Outcome", "outcome"], ["Reason", "reason"], ["Attempt spend", "cost_usd"], ["Committed actions", "committed_actions"]];

function readable(run: Run, key: string) {
  const value = run.metadata && key in run.metadata ? run.metadata[key] : run.summary?.[key] ?? (key === "model_name" ? run.summary?.agent : undefined);
  if (value == null || value === "") return "Unknown";
  if (key === "cost_usd" && typeof value === "number") return `$${value.toFixed(4)}`;
  return String(value);
}

export function Comparison({ data, onExplore, onBack }: { data: Data; onExplore?: (id: string) => void; onBack?: () => void }) {
  const [original, branch] = data.runs;
  return <main className="branch-comparison">
    <div className="comparison-heading"><div><p className="eyebrow">RUN COMPARISON</p><h1>Alternative continuation</h1></div><div>{onBack && <button onClick={onBack}>Back to runs</button>}</div></div>
    <p>{data.interpretation}</p>
    <p className="notice">This is a continuation from the original run{data.branch_decision == null ? "" : ` at decision ${data.branch_decision + 1}`}. Outcomes and model identity are exposed for both runs; an alternative outcome does not establish a causal effect.</p>
    <p className="muted">Comparison provenance · assistance: {data.assistance || "Unknown"} · branch decision: {data.branch_decision == null ? "Unknown" : data.branch_decision + 1}</p>
    {(original || branch) && <section className="panel"><h2>Recorded differences</h2><div className="comparison-run-headings"><h3>Original run</h3><h3>Branch</h3></div><div className="table-wrap"><table><thead><tr><th>Measure</th><th>Original run</th><th>Branch</th></tr></thead><tbody>
      <tr><th>Episode</th>{[original, branch].map((run) => <td key={run?.episode_id}>{run ? <><code>{run.episode_id.slice(0, 12)}</code>{onExplore && <> <button onClick={() => onExplore(run.episode_id)}>Explore run</button></>}</> : "Unknown"}</td>)}</tr>
      {FIELDS.map(([label, key]) => <tr key={key}><th>{label}</th>{[original, branch].map((run, index) => <td key={run?.episode_id ?? index}>{run ? readable(run, key) : "Unknown"}</td>)}</tr>)}</tbody></table></div>
      <details><summary>Exact technical summaries</summary>{data.runs.map((run, index) => <section key={run.episode_id}><h3>{index === 0 ? "Original run" : "Alternative continuation"}</h3><pre>{JSON.stringify(run.summary, null, 2)}</pre></section>)}</details>
    </section>}
    {data.runs.map((run, index) => <section key={run.episode_id}><h2>{index === 0 ? "Original run trajectory" : "Alternative continuation trajectory"}</h2><Trajectory points={run.trajectory} /></section>)}
  </main>;
}
