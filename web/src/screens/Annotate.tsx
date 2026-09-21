import { useState } from "react";
import { listAnnotations, saveAnnotation, type View } from "../api/client";

export function Annotate({ token, view, annotations, setAnnotations, busy, run }: { token: string; view: View; annotations: any[]; setAnnotations: (items: any[]) => void; busy: boolean; run: (task: () => Promise<void>) => Promise<void> }) {
  const [evidenceIds, setEvidenceIds] = useState("");
  const [judgment, setJudgment] = useState("unclear");
  const [note, setNote] = useState("");
  const [alternative, setAlternative] = useState("");
  const [start, setStart] = useState(view.decision);
  const [horizons, setHorizons] = useState<string[]>([]);
  const [confidence, setConfidence] = useState("medium");
  const [saved, setSaved] = useState("");
  const [editing, setEditing] = useState<string | null>(null);

  function edit(annotation: any) {
    setEditing(annotation.annotation_id); setNote(annotation.mechanism_summary); setStart(annotation.start_decision);
    setJudgment(annotation.judgment); setConfidence(annotation.confidence); setHorizons(annotation.horizons_in_tension);
    setAlternative(annotation.alternative_actions.join("\n")); setEvidenceIds(annotation.evidence_event_ids.join(", "));
  }
  return <section className="panel annotation">
    <p className="eyebrow">YOUR HORIZON ANALYSIS</p><h3>{editing ? "Revise annotation" : "Capture your assessment"}</h3>
    <div className="form-row"><label>From decision<input type="number" min={0} max={view.decision} value={start} onChange={(e) => setStart(+e.target.value)} /></label><label>Through decision<input readOnly value={view.decision} /></label><label>Assessment<select value={judgment} onChange={(e) => setJudgment(e.target.value)}>{["acceptable", "concern", "likely_error", "unclear"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Confidence<select value={confidence} onChange={(e) => setConfidence(e.target.value)}>{["low", "medium", "high"].map((value) => <option key={value}>{value}</option>)}</select></label></div>
    <div className="horizons">{["immediate", "near_term", "long_term"].map((horizon) => <label key={horizon}><input type="checkbox" checked={horizons.includes(horizon)} onChange={() => setHorizons((old) => old.includes(horizon) ? old.filter((item) => item !== horizon) : [...old, horizon])} />{horizon.replaceAll("_", " ")}</label>)}</div>
    <label>What tradeoff do you see?<textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Describe the mechanism and what the available evidence supports." /></label>
    <label>Alternative continuation<textarea value={alternative} onChange={(e) => setAlternative(e.target.value)} placeholder="What would you try, and why?" /></label>
    <label>Supporting event IDs<input value={evidenceIds} onChange={(e) => setEvidenceIds(e.target.value)} placeholder="Optional event IDs from the revealed trace, separated by commas" /></label>
    <button disabled={busy || !note} onClick={() => run(async () => { const result = await saveAnnotation(token, { start_decision: start, end_decision: view.decision, judgment, horizons_in_tension: horizons, mechanism_summary: note, alternative_actions: alternative ? [alternative] : [], confidence, evidence_event_ids: evidenceIds.split(",").map((value) => value.trim()).filter(Boolean), annotation_id: editing }); setSaved("Saved revision " + result.revision); setEditing(result.annotation_id); setAnnotations(await listAnnotations(token)); })}>{editing ? "Save revision" : "Save assessment"}</button>
    <span className="success">{saved}</span>
    {annotations.length > 0 && <details><summary>Annotations and revisions ({annotations.length})</summary>{annotations.map((annotation) => <article key={annotation.annotation_id + "-" + annotation.revision}><p>{annotation.mechanism_summary}</p><small>Revision {annotation.revision} · {annotation.review_mode}</small><button onClick={() => edit(annotation)}>Revise</button></article>)}</details>}
  </section>;
}
