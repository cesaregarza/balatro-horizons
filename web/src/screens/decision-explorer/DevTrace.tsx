import { useEffect, useState } from "react";
import { decisionTrace } from "../../api/client";
import { CallDetails } from "./DevTraceCall";
import { JsonPanel } from "./DevTraceValues";
import {
  objectNames,
  resourceChanges,
  type Trace,
} from "../../devTracePresentation";
import "../../devTrace.css";

export function DevTrace({
  token,
  decision,
  liveUpdates,
  refresh,
}: {
  token: string;
  decision: number;
  liveUpdates: boolean;
  refresh: number;
}) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = (delay: number) => {
      if (active && liveUpdates) timer = setTimeout(load, delay);
    };
    async function load() {
      if (document.hidden) {
        schedule(2000);
        return;
      }
      try {
        const data = await decisionTrace(token, decision, controller.signal) as Trace;
        if (!active) return;
        setTrace(data);
        setError("");
        if (!data.complete) schedule(2000);
      } catch (failure) {
        if (active) {
          setError(String(failure));
          schedule(5000);
        }
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [token, decision, liveUpdates, refresh]);
  return (
    <section className="dev-trace" aria-label="Model tool calls">
      <h3>Model tool calls · Decision {decision + 1}</h3>
      <p className="muted">
        Recorded requests, returned calls, and harness results. Opening this
        inspector records retrospective exposure.
      </p>
      {error && <p role="alert">{error}</p>}
      {!trace && !error && <p role="status">Loading model calls…</p>}
      {trace && (
        <>
          <p>
            {trace.calls.length} provider requests ·{" "}
            {trace.complete
              ? "Decision closed"
              : liveUpdates
                ? "Live updates on"
                : "Live updates paused"}
          </p>
          {!trace.calls.length && (
            <p>
              No provider request recorded yet. Scripted and human decisions may
              have only journal events.
            </p>
          )}
          <ol className="dev-calls">
            {trace.calls.map((call, index) => (
              <CallDetails
                key={call.request_id}
                call={call}
                index={index}
                names={objectNames(trace.observation)}
                contextEvent={trace.events.find(
                  (event) => event.event_id === call.context_event_id,
                )}
              />
            ))}
          </ol>
          <div className="dev-settled">
            <h4>Observed result of this decision</h4>
            {trace.transition ? (
              <>
                <p>A settled public state was recorded after this decision.</p>
                {resourceChanges(trace.observation, trace.transition).map(
                  (change) => (
                    <p key={change.label}>
                      <strong>{change.label}:</strong> {change.before} →{" "}
                      {change.after}
                    </p>
                  ),
                )}
                <p className="dev-meta">
                  Changes describe the whole decision, not each helper call. The
                  board shows the complete public state.
                </p>
              </>
            ) : (
              <p>
                No settled state recorded after this decision
                {trace.complete ? "." : " yet."}
              </p>
            )}
          </div>
          <JsonPanel
            label="All recorded events for this decision"
            value={trace.events}
          />
          <JsonPanel
            label="Public state before decision"
            value={trace.observation}
          />
          <JsonPanel
            label="Settled public state after decision"
            value={trace.transition}
          />
          <p className="muted">{trace.linkage}</p>
          {trace.omissions.map((text) => (
            <p className="muted" key={text}>
              {text}
            </p>
          ))}
        </>
      )}
    </section>
  );
}
