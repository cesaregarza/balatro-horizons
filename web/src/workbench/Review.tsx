import { useEffect, useState } from "react";
import { advanceReview, branchCapability, createBranch, listAnnotations, verifyContinuation, type Action, type View } from "../api/client";
import { Board } from "../Board";
import { ReasoningSummaries } from "./ReasoningSummaries";
import { Trajectory } from "./Trajectory";
import { Annotate } from "../screens/Annotate";

export function Review({ token, initial, onBranch, onExplore }: { token: string; initial: View; onBranch: (id: string) => void; onExplore: (decision: number) => void }) {
  const [view, setView] = useState(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [branching, setBranching] = useState(false);
  const [capability, setCapability] = useState<{ enabled: boolean; reason: string | null }>({ enabled: false, reason: null });
  const [annotations, setAnnotations] = useState<any[]>([]);
  const [replayMode, setReplayMode] = useState(initial.evidence_kind === "NATIVE" ? "seed_prefix" : "checkpoint");

  useEffect(() => {
    branchCapability(token).then(setCapability).catch(() => {});
    listAnnotations(token).then(setAnnotations).catch(() => {});
  }, [token, view]);
  async function run(task: () => Promise<void>) {
    setBusy(true); setError("");
    try { await task(); } catch (caught) { setError(String(caught)); } finally { setBusy(false); }
  }
  async function branch(mode: string, actions: Action[] = []) {
    await run(async () => { const result = await createBranch({ episode_id: view.episode_id, decision: view.decision, mode, actions }); onBranch(result.episode_id); });
  }
  return <div>
    <div className="review-top"><div><p className="eyebrow">DECISION {view.decision + 1} · {view.evidence_kind === "NATIVE" ? "NATIVE RUN" : "SYNTHETIC TEST"}</p><h2>What was knowable here?</h2></div><span className="badge">{view.review_mode} review</span></div>
    <p><button onClick={() => onExplore(view.decision)}>Explore full run</button> <span className="muted">Jump between decisions. Reveals the whole run and records outcome exposure.</span></p>
    {view.fixture && <p className="notice">Native evaluator fixture: {view.fixture}. Test setup may alter game state. Excluded from benchmark scores.</p>}
    {!view.fixture && !view.evaluation_eligible && <p className="muted">Calibration, synthetic, or assisted episode · excluded from autonomous benchmark scores.</p>}
    <div className="stages">{["observation", "action", "transition"].map((stage) => <span key={stage} className={stage === view.stage ? "current" : ""}>{stage === "observation" ? "1 · Available information" : stage === "action" ? "2 · Agent action" : "3 · Consequences"}</span>)}</div>
    {view.review_mode !== "prospective" && <p className="notice">Your exposure history is recorded with this review. Earlier labels remain unchanged.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <Board key={view.decision} observation={view.observation} />
    {view.action_events && <section className="panel"><h3>Recorded decision</h3>{view.action_events.filter((event) => ["action_commit", "action_rejected", "provider_error"].includes(event.type)).map((event, index) => <pre key={index}>{JSON.stringify(event.payload, null, 2)}</pre>)}<ReasoningSummaries events={view.action_events} /><details><summary>Exact agent-visible input and operations</summary><pre>{JSON.stringify(view.action_events, null, 2)}</pre></details></section>}
    {view.transition && <section className="panel"><h3>After the action</h3><div className="stats">{["money", "hands", "discards", "chips"].map((key) => <div key={key}><small>{key}</small><strong>{view.observation.state.resources[key] ?? "?"} → {view.transition!.state.resources[key] ?? "?"}</strong></div>)}</div><Board observation={view.transition} /></section>}
    {view.terminal && <section className="panel"><h3>Run ended</h3><pre>{JSON.stringify(view.terminal, null, 2)}</pre></section>}
    <Trajectory points={view.trajectory || []} />
    <Annotate key={`${token}:${view.decision}`} token={token} view={view} annotations={annotations} setAnnotations={setAnnotations} busy={busy} run={run} />
    <div className="review-footer"><button className="primary" disabled={busy || (view.stage === "transition" && !view.can_advance)} onClick={() => run(async () => setView(await advanceReview(token)))}>{view.stage === "observation" ? "Reveal agent action" : view.stage === "action" ? "Reveal consequences" : "Next decision"}</button><select aria-label="Restoration method" value={replayMode} onChange={(e) => setReplayMode(e.target.value)}><option value="checkpoint">Native save</option><option value="seed_prefix">Seed-prefix replay</option></select><button disabled={busy} onClick={() => run(async () => { const cert = await verifyContinuation({ episode_id: view.episode_id, decision: view.decision, mode: replayMode }); setCapability({ enabled: cert.status === "passed", reason: cert.status === "passed" ? "" : "Replay diverged; branching remains disabled." }); })}>Verify continuation</button><button disabled={busy || !capability.enabled} onClick={() => setBranching(!branching)}>Explore an alternative</button></div>
    {!capability.enabled && <p className="muted">{capability.reason}</p>}
    {branching && <section className="panel"><h3>Branch from this decision</h3><p>The original run stays unchanged. Choose one alternative action below, or resume control.</p><div className="actions"><button onClick={() => branch("agent_continue")}>Resume agent</button><button onClick={() => branch("human_takeover")}>Take over</button><button onClick={() => branch("short_human_sequence")}>Play 3 actions, then resume agent</button></div><Board observation={view.observation} onAction={(action) => branch("single_action_override", [action])} /></section>}
  </div>;
}
