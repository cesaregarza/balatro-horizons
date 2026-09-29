import { useEffect, useRef, useState } from "react";
import { listAnnotations, listReviewAnnotations, saveAnnotation, saveReviewAnnotation, type View } from "../api/client";

export function Annotate({ token, view, annotations, setAnnotations, busy, run, route = "explore", onDirtyChange }: {
  token: string; view: View; annotations: any[]; setAnnotations: (items: any[]) => void;
  busy: boolean; run: (task: () => Promise<void>) => Promise<void>; route?: "explore" | "review";
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [evidenceIds, setEvidenceIds] = useState("");
  const [judgment, setJudgment] = useState("unclear");
  const [note, setNote] = useState("");
  const [alternative, setAlternative] = useState("");
  const [start, setStart] = useState(String(view.decision + 1));
  const [end, setEnd] = useState(view.decision + 1);
  const [anchorDecision, setAnchorDecision] = useState(view.decision + 1);
  const [horizons, setHorizons] = useState<string[]>([]);
  const [confidence, setConfidence] = useState("medium");
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const editor = useRef<HTMLElement>(null);
  const saveLock = useRef(false);
  const dirtyCallback = useRef(onDirtyChange);
  dirtyCallback.current = onDirtyChange;

  useEffect(() => { dirtyCallback.current?.(dirty); }, [dirty]);
  useEffect(() => () => dirtyCallback.current?.(false), []);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (!dirty && !editing) { setStart(String(view.decision + 1)); setEnd(view.decision + 1); setAnchorDecision(view.decision + 1); }
  }, [view.decision, dirty, editing]);

  function changed() { setDirty(true); setSaved(""); }
  function edit(annotation: any) {
    if (dirty && !window.confirm("Discard the unsaved annotation draft and load this revision?")) return;
    setEditing(annotation.annotation_id); setNote(annotation.mechanism_summary);
    setStart(String(annotation.start_decision + 1)); setEnd(annotation.end_decision + 1);
    setJudgment(annotation.judgment); setConfidence(annotation.confidence); setHorizons(annotation.horizons_in_tension);
    setAlternative(annotation.alternative_actions.join("\n")); setEvidenceIds(annotation.evidence_event_ids.join(", "));
    setDirty(false); setSaved(""); setError("");
    requestAnimationFrame(() => editor.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus());
  }
  const validStart = Number.isInteger(Number(start)) && Number(start) >= 1 && Number(start) <= end;
  function saveError(caught: unknown) {
    const message = String(caught);
    setError(/TOKEN|SESSION|EXPIRED|UNAUTHORIZED|403|401/.test(message)
      ? "This inspection session has expired. Your draft is still here; reopen the run in another tab to start a fresh session, then copy your draft before saving."
      : "Could not save this assessment. Your draft is still here. Try saving again.");
  }
  async function save() {
    if (!validStart || busy || saveLock.current) return;
    saveLock.current = true;
    setError("");
    try {
      await run(async () => {
        try {
          const persist = route === "review" ? saveReviewAnnotation : saveAnnotation;
          const result = await persist(token, {
            start_decision: Number(start) - 1, end_decision: end - 1, judgment,
            horizons_in_tension: horizons, mechanism_summary: note,
            alternative_actions: alternative ? [alternative] : [], confidence,
            evidence_event_ids: evidenceIds.split(",").map((value) => value.trim()).filter(Boolean), annotation_id: editing,
          });
          setSaved("Saved revision " + result.revision); setEditing(result.annotation_id); setDirty(false);
          setAnnotations(await (route === "review" ? listReviewAnnotations(token) : listAnnotations(token, anchorDecision - 1)));
        } catch (caught) { saveError(caught); }
      });
    } catch (caught) { saveError(caught); } finally { saveLock.current = false; }
  }

  return <section ref={editor} className="panel annotation" onChange={changed}>
    <p className="eyebrow">YOUR HORIZON ANALYSIS</p><h3>{editing ? "Revise annotation" : "Capture your assessment"}</h3>
    <p>Selected decision #{anchorDecision}{dirty ? " · Unsaved draft" : ""}</p>
    <div className="form-row"><label>From decision<input type="number" min={1} max={end} value={start} onChange={(e) => setStart(e.target.value)} /></label><label>Through decision<input readOnly value={end} /></label><label>Assessment<select value={judgment} onChange={(e) => setJudgment(e.target.value)}>{["acceptable", "concern", "likely_error", "unclear"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Confidence<select value={confidence} onChange={(e) => setConfidence(e.target.value)}>{["low", "medium", "high"].map((value) => <option key={value}>{value}</option>)}</select></label></div>
    {!validStart && <p className="error" role="alert">Choose a whole decision number from 1 through {end}.</p>}
    <div className="horizons">{["immediate", "near_term", "long_term"].map((horizon) => <label key={horizon}><input type="checkbox" checked={horizons.includes(horizon)} onChange={() => setHorizons((old) => old.includes(horizon) ? old.filter((item) => item !== horizon) : [...old, horizon])} />{horizon.replaceAll("_", " ")}</label>)}</div>
    <label>What tradeoff do you see?<textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Describe the mechanism and what the available evidence supports." /></label>
    <label>Alternative continuation<textarea value={alternative} onChange={(e) => setAlternative(e.target.value)} placeholder="What would you try, and why?" /></label>
    <label>Supporting event IDs<input value={evidenceIds} onChange={(e) => setEvidenceIds(e.target.value)} placeholder="Optional event IDs from the revealed trace, separated by commas" /></label>
    <button disabled={busy || !note.trim() || !validStart} onClick={() => void save()}>{editing ? "Save revision" : "Save assessment"}</button>
    <span className="success" role="status">{saved}</span>
    {error && <p className="error" role="alert">{error}</p>}
    {annotations.length > 0 && <details><summary>Annotations and revisions ({annotations.length})</summary>{annotations.map((annotation) => <article key={annotation.annotation_id + "-" + annotation.revision}><p>{annotation.mechanism_summary}</p><small>Decisions #{annotation.start_decision + 1}–#{annotation.end_decision + 1} · Revision {annotation.revision} · {annotation.review_mode}</small><button onClick={() => edit(annotation)}>Revise</button></article>)}</details>}
  </section>;
}
