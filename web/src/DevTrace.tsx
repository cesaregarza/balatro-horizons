import { useEffect, useState } from "react";
import { api } from "./api";
import { humanize } from "./decisionPresentation";
import "./devTrace.css";

type Data = Record<string, any>;
type Event = {
  event_id: string;
  sequence: number;
  timestamp: string;
  type: string;
  payload: Data;
};
type Call = {
  request_id: string;
  request_event: Event;
  context_event_id: string | null;
  response_event: Event | null;
  error_event: Event | null;
  status: string;
  tools: {
    call_id: string | null;
    name: string | null;
    arguments: unknown;
    raw_arguments: unknown;
    arguments_parse_error: boolean;
    delivered_results: {
      request_id: string;
      content: unknown;
      content_parse_error: boolean;
    }[];
  }[];
  journal_events: Event[];
};
type Trace = {
  decision: number;
  complete: boolean;
  calls: Call[];
  events: Event[];
  observation: unknown;
  transition: unknown;
  omissions: string[];
  linkage: string;
};

function dollars(value: unknown) {
  return value != null && Number.isFinite(Number(value))
    ? `$${Number(value).toFixed(4)}`
    : "unknown";
}

// Large request bodies are rendered only when expanded. React escapes all text.
function JsonPanel({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="dev-json"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>{label}</summary>
      {open && <pre>{JSON.stringify(value, null, 2) ?? "Not recorded"}</pre>}
    </details>
  );
}

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
        const data = await api<Trace>(
          `/review/decisions/${decision}/trace`,
          "GET",
          undefined,
          token,
          controller.signal,
        );
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
              <li key={call.request_id} className="dev-call">
                <h4>
                  Call {index + 1} ·{" "}
                  {call.tools
                    .map((tool) => tool.name || "unnamed tool")
                    .join(", ") || "No tool call returned"}
                </h4>
                <p className="dev-status">
                  {humanize(call.status)} · transport attempt{" "}
                  {call.request_event.payload.attempt ?? "unknown"}
                </p>
                <p className="dev-meta">
                  {call.request_event.timestamp} · Request {call.request_id}
                </p>
                {call.response_event && (
                  <p className="dev-meta">
                    Response {call.response_event.timestamp} · recorded cost{" "}
                    {dollars(call.response_event.payload.cost_usd)}
                  </p>
                )}
                {!call.response_event && (
                  <p className="dev-meta">
                    Reserved {dollars(call.request_event.payload.reserved_usd)}{" "}
                    · final usage not recorded
                  </p>
                )}
                {call.error_event && (
                  <JsonPanel
                    label="Transport error"
                    value={call.error_event.payload}
                  />
                )}
                {call.tools.map((tool, ordinal) => (
                  <div key={`${tool.call_id}-${ordinal}`} className="dev-tool">
                    <strong>{tool.name || "Unnamed tool"}</strong>
                    <p className="dev-meta">
                      Tool call ID: {tool.call_id ?? "not recorded"}
                    </p>
                    {tool.arguments_parse_error && (
                      <p className="error">
                        Arguments are not valid JSON; original text is
                        preserved.
                      </p>
                    )}
                    <JsonPanel label="Tool arguments" value={tool.arguments} />
                    {tool.arguments_parse_error && (
                      <JsonPanel
                        label="Original argument string"
                        value={tool.raw_arguments}
                      />
                    )}
                    {tool.delivered_results.map((result, i) => (
                      <JsonPanel
                        key={i}
                        label="Result sent back to model (matched call ID)"
                        value={result}
                      />
                    ))}
                  </div>
                ))}
                <div className="dev-results">
                  {call.journal_events.map((event) => (
                    <JsonPanel
                      key={event.event_id}
                      label={`Journal: ${humanize(event.type)} · event ${event.sequence}`}
                      value={event}
                    />
                  ))}
                </div>
                <JsonPanel
                  label="Full recorded provider request"
                  value={call.request_event}
                />
                {call.response_event && (
                  <JsonPanel
                    label="Full recorded provider response"
                    value={call.response_event}
                  />
                )}
                <JsonPanel
                  label="Delivered context and helper exchanges"
                  value={
                    trace.events.find(
                      (event) => event.event_id === call.context_event_id,
                    )?.payload
                  }
                />
                {call.response_event?.payload.body?.usage && (
                  <JsonPanel
                    label="Token usage and cache accounting"
                    value={call.response_event.payload.body.usage}
                  />
                )}
              </li>
            ))}
          </ol>
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
