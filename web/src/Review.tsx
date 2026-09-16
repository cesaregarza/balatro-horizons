import { useEffect, useState } from "react";
import { api, type View, type Action } from "./api";
import { Board } from "./Board";
import { Trajectory } from "./Trajectory";
import { ReasoningSummaries } from "./ReasoningSummaries";
export function Review({
  token,
  initial,
  onBranch,
  onExplore,
}: {
  token: string;
  initial: View;
  onBranch: (id: string) => void;
  onExplore: (decision: number) => void;
}) {
  const [view, setView] = useState(initial),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [evidenceIds, setEvidenceIds] = useState("");
  const [judgment, setJudgment] = useState("unclear"),
    [note, setNote] = useState(""),
    [alternative, setAlternative] = useState("");
  const [start, setStart] = useState(initial.decision),
    [horizons, setHorizons] = useState<string[]>([]),
    [confidence, setConfidence] = useState("medium");
  const [saved, setSaved] = useState(""),
    [branching, setBranching] = useState(false),
    [capability, setCapability] = useState({ enabled: false, reason: "" });
  const [annotations, setAnnotations] = useState<any[]>([]),
    [editing, setEditing] = useState<string | null>(null);
  const [replayMode, setReplayMode] = useState(
    initial.evidence_kind === "NATIVE" ? "seed_prefix" : "checkpoint",
  );
  useEffect(() => {
    api("/review/branch-capability", "GET", undefined, token)
      .then(setCapability)
      .catch(() => {});
    api("/review/annotations", "GET", undefined, token)
      .then(setAnnotations)
      .catch(() => {});
  }, [token, view]);
  async function run(f: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await f();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function branch(mode: string, actions: Action[] = []) {
    await run(async () => {
      const b = await api("/branches", "POST", {
        episode_id: view.episode_id,
        decision: view.decision,
        mode,
        actions,
      });
      onBranch(b.episode_id);
    });
  }
  return (
    <div>
      <div className="review-top">
        <div>
          <p className="eyebrow">
            DECISION {view.decision + 1} ·{" "}
            {view.evidence_kind === "NATIVE" ? "NATIVE RUN" : "SYNTHETIC TEST"}
          </p>
          <h2>What was knowable here?</h2>
        </div>
        <span className="badge">{view.review_mode} review</span>
      </div>
      <p>
        <button onClick={() => onExplore(view.decision)}>
          Explore full run
        </button>{" "}
        <span className="muted">
          Jump between decisions. Reveals the whole run and records outcome
          exposure.
        </span>
      </p>
      {view.fixture && (
        <p className="notice">
          Native evaluator fixture: {view.fixture}. Test setup may alter game
          state. Excluded from benchmark scores.
        </p>
      )}
      {!view.fixture && !view.evaluation_eligible && (
        <p className="muted">
          Calibration, synthetic, or assisted episode · excluded from autonomous
          benchmark scores.
        </p>
      )}
      <div className="stages">
        {["observation", "action", "transition"].map((s) => (
          <span key={s} className={s === view.stage ? "current" : ""}>
            {s === "observation"
              ? "1 · Available information"
              : s === "action"
                ? "2 · Agent action"
                : "3 · Consequences"}
          </span>
        ))}
      </div>
      {view.review_mode !== "prospective" && (
        <p className="notice">
          Your exposure history is recorded with this review. Earlier labels
          remain unchanged.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <Board key={view.decision} observation={view.observation} />
      {view.action_events && (
        <section className="panel">
          <h3>Recorded decision</h3>
          {view.action_events
            .filter((e) =>
              ["action_commit", "action_rejected", "provider_error"].includes(
                e.type,
              ),
            )
            .map((e, i) => (
              <pre key={i}>{JSON.stringify(e.payload, null, 2)}</pre>
            ))}
          <ReasoningSummaries events={view.action_events} />
          <details>
            <summary>Exact agent-visible input and operations</summary>
            <pre>{JSON.stringify(view.action_events, null, 2)}</pre>
          </details>
        </section>
      )}
      {view.transition && (
        <section className="panel">
          <h3>After the action</h3>
          <div className="stats">
            {["money", "hands", "discards", "chips"].map((k) => (
              <div key={k}>
                <small>{k}</small>
                <strong>
                  {view.observation.state.resources[k] ?? "?"} →{" "}
                  {view.transition!.state.resources[k] ?? "?"}
                </strong>
              </div>
            ))}
          </div>
          <Board observation={view.transition} />
        </section>
      )}
      {view.terminal && (
        <section className="panel">
          <h3>Run ended</h3>
          <pre>{JSON.stringify(view.terminal, null, 2)}</pre>
        </section>
      )}
      <Trajectory points={view.trajectory || []} />
      <section className="panel annotation">
        <p className="eyebrow">YOUR HORIZON ANALYSIS</p>
        <h3>{editing ? "Revise annotation" : "Capture your assessment"}</h3>
        <div className="form-row">
          <label>
            From decision
            <input
              type="number"
              min={0}
              max={view.decision}
              value={start}
              onChange={(e) => setStart(+e.target.value)}
            />
          </label>
          <label>
            Through decision
            <input readOnly value={view.decision} />
          </label>
          <label>
            Assessment
            <select
              value={judgment}
              onChange={(e) => setJudgment(e.target.value)}
            >
              {["acceptable", "concern", "likely_error", "unclear"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label>
            Confidence
            <select
              value={confidence}
              onChange={(e) => setConfidence(e.target.value)}
            >
              {["low", "medium", "high"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="horizons">
          {["immediate", "near_term", "long_term"].map((h) => (
            <label key={h}>
              <input
                type="checkbox"
                checked={horizons.includes(h)}
                onChange={() =>
                  setHorizons((old) =>
                    old.includes(h) ? old.filter((x) => x !== h) : [...old, h],
                  )
                }
              />
              {h.replaceAll("_", " ")}
            </label>
          ))}
        </div>
        <label>
          What tradeoff do you see?
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Describe the mechanism and what the available evidence supports."
          />
        </label>
        <label>
          Alternative continuation
          <textarea
            value={alternative}
            onChange={(e) => setAlternative(e.target.value)}
            placeholder="What would you try, and why?"
          />
        </label>
        <label>
          Supporting event IDs
          <input
            value={evidenceIds}
            onChange={(e) => setEvidenceIds(e.target.value)}
            placeholder="Optional event IDs from the revealed trace, separated by commas"
          />
        </label>
        <button
          disabled={busy || !note}
          onClick={() =>
            run(async () => {
              const r = await api(
                "/review/annotations",
                "POST",
                {
                  start_decision: start,
                  end_decision: view.decision,
                  judgment,
                  horizons_in_tension: horizons,
                  mechanism_summary: note,
                  alternative_actions: alternative ? [alternative] : [],
                  confidence,
                  evidence_event_ids: evidenceIds
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                  annotation_id: editing,
                },
                token,
              );
              setSaved("Saved revision " + r.revision);
              setEditing(r.annotation_id);
              setAnnotations(
                await api("/review/annotations", "GET", undefined, token),
              );
            })
          }
        >
          {editing ? "Save revision" : "Save assessment"}
        </button>
        <span className="success">{saved}</span>
        {annotations.length > 0 && (
          <details>
            <summary>Annotations and revisions ({annotations.length})</summary>
            {annotations.map((a) => (
              <article key={a.annotation_id + "-" + a.revision}>
                <p>{a.mechanism_summary}</p>
                <small>
                  Revision {a.revision} · {a.review_mode}
                </small>
                <button
                  onClick={() => {
                    setEditing(a.annotation_id);
                    setNote(a.mechanism_summary);
                    setStart(a.start_decision);
                    setJudgment(a.judgment);
                    setConfidence(a.confidence);
                    setHorizons(a.horizons_in_tension);
                    setAlternative(a.alternative_actions.join("\n"));
                    setEvidenceIds(a.evidence_event_ids.join(", "));
                  }}
                >
                  Revise
                </button>
              </article>
            ))}
          </details>
        )}
      </section>
      <div className="review-footer">
        <button
          className="primary"
          disabled={busy || (view.stage === "transition" && !view.can_advance)}
          onClick={() =>
            run(async () => {
              const next = await api<View>(
                "/review/advance",
                "POST",
                {},
                token,
              );
              setView(next);
              if (next.stage === "observation") {
                setStart(next.decision);
                setNote("");
                setSaved("");
                setEditing(null);
                setEvidenceIds("");
              }
            })
          }
        >
          {view.stage === "observation"
            ? "Reveal agent action"
            : view.stage === "action"
              ? "Reveal consequences"
              : "Next decision"}
        </button>
        <select
          aria-label="Restoration method"
          value={replayMode}
          onChange={(e) => setReplayMode(e.target.value)}
        >
          <option value="checkpoint">Native save</option>
          <option value="seed_prefix">Seed-prefix replay</option>
        </select>
        <button
          disabled={busy}
          onClick={() =>
            run(async () => {
              const cert = await api("/verify", "POST", {
                episode_id: view.episode_id,
                decision: view.decision,
                mode: replayMode,
              });
              setCapability({
                enabled: cert.status === "passed",
                reason:
                  cert.status === "passed"
                    ? ""
                    : "Replay diverged; branching remains disabled.",
              });
            })
          }
        >
          Verify continuation
        </button>
        <button
          disabled={busy || !capability.enabled}
          onClick={() => setBranching(!branching)}
        >
          Explore an alternative
        </button>
      </div>
      {!capability.enabled && <p className="muted">{capability.reason}</p>}
      {branching && (
        <section className="panel">
          <h3>Branch from this decision</h3>
          <p>
            The original run stays unchanged. Choose one alternative action
            below, or resume control.
          </p>
          <div className="actions">
            <button onClick={() => branch("agent_continue")}>
              Resume agent
            </button>
            <button onClick={() => branch("human_takeover")}>Take over</button>
            <button onClick={() => branch("short_human_sequence")}>
              Play 3 actions, then resume agent
            </button>
          </div>
          <Board
            observation={view.observation}
            onAction={(a) => branch("single_action_override", [a])}
          />
        </section>
      )}
    </div>
  );
}
