import { useState } from "react";
import {
  gameMoney,
  record,
  title,
  toolTitle,
  quotedPrice,
  statusText,
  type Data,
  type TraceCall,
  type TraceEvent,
  type TraceTool,
} from "./devTracePresentation";

// Raw bodies stay lazy; all model-authored text is escaped by React.
export function JsonPanel({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="dev-json"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>{label}</summary>
      {open && <pre>{JSON.stringify(value, null, 2) ?? "Not recorded"}</pre>}
    </details>
  );
}

/** Paged, escaped values for unfamiliar tools as well as ordinary helper results. */
export function ReadableValue({
  value,
  names = new Map(),
  depth = 0,
}: {
  value: unknown;
  names?: Map<string, string>;
  depth?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [count, setCount] = useState(8);
  if (value == null) return <span className="muted">Not recorded</span>;
  if (typeof value === "string") {
    // Tool-result text blocks often contain a serialized object. Never repair partial JSON.
    if (/^\s*[\[{]/.test(value)) {
      try {
        const decoded = JSON.parse(value);
        return <ReadableValue value={decoded} names={names} depth={depth} />;
      } catch {
        /* show verbatim */
      }
    }
    const text = names.get(value) || value;
    return (
      <span className="dev-value-text">
        {expanded ? text : text.slice(0, 600)}
        {text.length > 600 && (
          <button className="dev-expand" onClick={() => setExpanded(!expanded)}>
            {expanded
              ? "Show less"
              : `Read full text (${text.length.toLocaleString()} characters)`}
          </button>
        )}
      </span>
    );
  }
  if (typeof value !== "object")
    return (
      <span>
        {typeof value === "boolean" ? (value ? "Yes" : "No") : String(value)}
      </span>
    );
  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i + 1), v] as const)
    : Object.entries(value);
  if (!entries.length)
    return (
      <span className="muted">
        {Array.isArray(value) ? "None" : "No fields"}
      </span>
    );
  if (depth >= 2 && !expanded)
    return (
      <button className="dev-expand" onClick={() => setExpanded(true)}>
        Show {entries.length} {Array.isArray(value) ? "items" : "fields"}
      </button>
    );
  return (
    <div className="dev-value">
      <dl>
        {entries.slice(0, count).map(([key, child]) => (
          <div key={key}>
            <dt>{title(key)}</dt>
            <dd>
              <ReadableValue value={child} names={names} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
      {entries.length > count && (
        <button className="dev-expand" onClick={() => setCount(count + 16)}>
          Show more ({entries.length - count} remaining)
        </button>
      )}
    </div>
  );
}

function dollars(value: unknown) {
  return (typeof value === "number" ||
    (typeof value === "string" && value.trim())) &&
    Number.isFinite(Number(value))
    ? `$${Number(value).toFixed(4)}`
    : "unknown";
}

function JournalResult({
  event,
  names,
}: {
  event: TraceEvent;
  names: Map<string, string>;
}) {
  const p = event.payload;
  switch (event.type) {
    case "helper_result":
      return (
        <div className="dev-result">
          <strong>Helper returned</strong>
          <ReadableValue value={p.result} names={names} />
        </div>
      );
    case "run_note":
      return (
        <div className="dev-result">
          <strong>
            {p.operation === "delete_run_note"
              ? "Notebook entry deleted"
              : "Notebook saved"}
            : {p.key}
          </strong>
          {p.text != null && <ReadableValue value={p.text} />}
          <small>Revision {p.revision ?? "unknown"}</small>
        </div>
      );
    case "action_commit":
      return (
        <p className="dev-result">
          Game action committed: {title(record(p.action).type || "unknown")}.
          See the settled result below when available.
        </p>
      );
    case "action_intent":
      return (
        <p className="dev-meta">
          Game action submitted: {title(record(p.action).type || "unknown")}.
          Submission alone does not confirm completion.
        </p>
      );
    case "action_rejected":
    case "harness_failure":
      return (
        <div className="dev-result dev-error">
          <strong>
            {event.type === "action_rejected"
              ? "Request rejected"
              : "Harness failure"}
          </strong>
          <ReadableValue value={p} names={names} />
        </div>
      );
    default:
      return null;
  }
}

function RequestedTool({
  tool,
  ordinal,
  context,
  names,
}: {
  tool: TraceTool;
  ordinal: number;
  context: Data;
  names: Map<string, string>;
}) {
  const args = record(tool.arguments);
  const fields = Object.fromEntries(
    Object.entries(args).filter(
      ([key]) =>
        ![
          "observation_id",
          "decision_note",
          "memory_update",
          "note_update",
        ].includes(key),
    ),
  );
  const quote = quotedPrice(tool, context);
  return (
    <section className="dev-tool" aria-label={`Requested tool ${ordinal + 1}`}>
      <strong>Requested: {toolTitle(tool, names)}</strong>
      {quote && (
        <p className="dev-quote">
          {quote}
          {context.current_costs?.cash_balance != null &&
            ` · Cash balance: ${gameMoney(context.current_costs.cash_balance)}`}
        </p>
      )}
      <p className="dev-meta">
        {tool.name || "Unnamed tool"} · Tool call ID:{" "}
        {tool.call_id ?? "not recorded"}
      </p>
      {tool.arguments_parse_error ? (
        <p className="dev-error">
          Arguments are not valid JSON; this request cannot be interpreted.
          Original text is in technical details.
        </p>
      ) : (
        <>
          {Object.keys(fields).length > 0 && (
            <ReadableValue value={fields} names={names} />
          )}
          {args.decision_note != null && (
            <div className="dev-model-note">
              <strong>Model’s note</strong>
              <ReadableValue value={args.decision_note} />
            </div>
          )}
          {(args.note_update != null || args.memory_update != null) && (
            <div className="dev-model-note">
              <strong>Requested memory update</strong>
              <ReadableValue value={args.note_update ?? args.memory_update} />
            </div>
          )}
        </>
      )}
      {tool.delivered_results.map((result, index) => (
        <div className="dev-result" key={index}>
          <strong>Sent back to model · matched tool call</strong>
          {result.content_parse_error && (
            <p>Result is plain text or could not be parsed as JSON.</p>
          )}
          <ReadableValue value={result.content} names={names} />
        </div>
      ))}
    </section>
  );
}

function DeliveredContext({
  context,
  present,
  names,
}: {
  context: Data;
  present: boolean;
  names: Map<string, string>;
}) {
  return (
    <details className="dev-context">
      <summary>
        What the model was given · costs, notebook and recent memory
      </summary>
      {present ? (
        <>
          <h5>Current costs</h5>
          <ReadableValue value={context.current_costs} names={names} />
          <h5>Run notebook</h5>
          <ReadableValue
            value={context.run_notebook?.entries ?? context.run_notebook}
          />
          <h5>Recent working memory</h5>
          <ReadableValue value={context.working_memory} />
        </>
      ) : (
        <p>No context event recorded for this request.</p>
      )}
    </details>
  );
}

function TechnicalDetails({
  call,
  contextEvent,
  usage,
}: {
  call: TraceCall;
  contextEvent?: TraceEvent;
  usage: Data;
}) {
  return (
    <details className="dev-technical">
      <summary>Technical details · raw JSON and IDs</summary>
      <p className="dev-meta">
        {call.request_event.timestamp} · Request {call.request_id}
      </p>
      {call.tools.map((tool, index) => (
        <div key={index}>
          <JsonPanel
            label={`Tool arguments · ${tool.name || "unnamed"}`}
            value={tool.arguments}
          />
          {tool.arguments_parse_error && (
            <JsonPanel
              label="Original argument string"
              value={tool.raw_arguments}
            />
          )}
          {tool.delivered_results.map((result, resultIndex) => (
            <JsonPanel
              key={resultIndex}
              label="Result sent back to model (matched call ID)"
              value={result}
            />
          ))}
        </div>
      ))}
      {call.journal_events.map((event) => (
        <JsonPanel
          key={event.event_id}
          label={`Journal: ${title(event.type)} · event ${event.sequence}`}
          value={event}
        />
      ))}
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
      {call.error_event && (
        <JsonPanel label="Transport error" value={call.error_event} />
      )}
      <JsonPanel
        label="Delivered context and helper exchanges"
        value={contextEvent?.payload}
      />
      <JsonPanel label="Token usage and cache accounting" value={usage} />
    </details>
  );
}

export function CallDetails({
  call,
  index,
  contextEvent,
  names,
}: {
  call: TraceCall;
  index: number;
  contextEvent?: TraceEvent;
  names: Map<string, string>;
}) {
  const context = record(contextEvent?.payload.context);
  const usage = record(call.response_event?.payload.body?.usage);
  const cached =
    record(usage.input_tokens_details).cached_tokens ??
    usage.cache_read_input_tokens;
  return (
    <li className="dev-call">
      <h4>
        Call {index + 1} ·{" "}
        {call.tools.map((t) => toolTitle(t, names)).join(" · ") ||
          "No tool call returned"}
      </h4>
      <p className="dev-status">{statusText(call.status)}</p>
      <p className="dev-meta">
        Transport attempt {call.request_event.payload.attempt ?? "unknown"} ·{" "}
        {call.response_event
          ? `API cost ${dollars(call.response_event.payload.cost_usd)}`
          : `Reserved ${dollars(call.request_event.payload.reserved_usd)}; final usage not recorded`}
        {usage.input_tokens != null &&
          ` · ${usage.input_tokens.toLocaleString()} input tokens`}
        {cached != null && ` · ${cached.toLocaleString()} cached`}
        {usage.output_tokens != null &&
          ` · ${usage.output_tokens.toLocaleString()} output tokens`}
      </p>
      {call.error_event && (
        <div className="dev-result dev-error">
          <strong>Provider error</strong>
          <ReadableValue value={call.error_event.payload} />
        </div>
      )}
      {call.tools.map((tool, ordinal) => (
        <RequestedTool
          key={`${tool.call_id}-${ordinal}`}
          tool={tool}
          ordinal={ordinal}
          context={context}
          names={names}
        />
      ))}
      {call.journal_events.some((e) => e.type !== "agent_operation") && (
        <div className="dev-results">
          <h5>Recorded by the harness</h5>
          <p className="dev-meta">
            Associated with this request by journal order. Multiple returned
            tools may all be rejected.
          </p>
          {call.journal_events.map((event) => (
            <JournalResult key={event.event_id} event={event} names={names} />
          ))}
        </div>
      )}
      {!call.tools.length && !call.error_event && (
        <p>
          {call.response_event
            ? "The provider returned no tool calls. Its full response is available in technical details."
            : "No model tool call has been recorded for this request."}
        </p>
      )}
      <DeliveredContext
        context={context}
        present={Boolean(contextEvent)}
        names={names}
      />
      <TechnicalDetails call={call} contextEvent={contextEvent} usage={usage} />
    </li>
  );
}
