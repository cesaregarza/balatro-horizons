import { gameMoney, record, title, toolTitle, quotedPrice, type Data, type TraceEvent, type TraceTool, type TraceCall } from "../../devTracePresentation";
import { JsonPanel, ReadableValue } from "./DevTraceValues";

function dollars(value: unknown) {
  return (typeof value === "number" || (typeof value === "string" && value.trim())) && Number.isFinite(Number(value)) ? `$${Number(value).toFixed(4)}` : "unknown";
}

export function JournalResult({ event, names }: { event: TraceEvent; names: Map<string, string> }) {
  const payload = event.payload;
  if (event.type === "helper_result") return <div className="dev-result"><strong>Helper returned</strong><ReadableValue value={payload.result} names={names} /></div>;
  if (event.type === "run_note") return <div className="dev-result"><strong>{payload.operation === "delete_run_note" ? "Notebook entry deleted" : "Notebook saved"}: {payload.key}</strong>{payload.text != null && <ReadableValue value={payload.text} />}<small>Revision {payload.revision ?? "unknown"}</small></div>;
  if (event.type === "action_commit") return <p className="dev-result">Game action committed: {title(record(payload.action).type || "unknown")}. See the settled result below when available.</p>;
  if (event.type === "action_intent") return <p className="dev-meta">Game action submitted: {title(record(payload.action).type || "unknown")}. Submission alone does not confirm completion.</p>;
  if (event.type === "action_rejected" || event.type === "harness_failure") return <div className="dev-result dev-error"><strong>{event.type === "action_rejected" ? "Request rejected" : "Harness failure"}</strong><ReadableValue value={payload} names={names} /></div>;
  return null;
}

export function RequestedTool({ tool, ordinal, context, names }: { tool: TraceTool; ordinal: number; context: Data; names: Map<string, string> }) {
  const args = record(tool.arguments);
  const fields = Object.fromEntries(Object.entries(args).filter(([key]) => !["observation_id", "decision_note", "memory_update", "note_update"].includes(key)));
  const quote = quotedPrice(tool, context);
  return <section className="dev-tool" aria-label={`Requested tool ${ordinal + 1}`}><strong>Requested: {toolTitle(tool, names)}</strong>{quote && <p className="dev-quote">{quote}{context.current_costs?.cash_balance != null && ` · Cash balance: ${gameMoney(context.current_costs.cash_balance)}`}</p>}<p className="dev-meta">{tool.name || "Unnamed tool"} · Tool call ID: {tool.call_id ?? "not recorded"}</p>{tool.arguments_parse_error ? <p className="dev-error">Arguments are not valid JSON; this request cannot be interpreted. Original text is in technical details.</p> : <>{Object.keys(fields).length > 0 && <ReadableValue value={fields} names={names} />}{args.decision_note != null && <div className="dev-model-note"><strong>Model’s note</strong><ReadableValue value={args.decision_note} /></div>}{(args.note_update != null || args.memory_update != null) && <div className="dev-model-note"><strong>Requested memory update</strong><ReadableValue value={args.note_update ?? args.memory_update} /></div>}</>}{tool.delivered_results.map((result, index) => <div className="dev-result" key={index}><strong>Sent back to model · matched tool call</strong>{result.content_parse_error && <p>Result is plain text or could not be parsed as JSON.</p>}<ReadableValue value={result.content} names={names} /></div>)}</section>;
}

export function DeliveredContext({ context, present, names }: { context: Data; present: boolean; names: Map<string, string> }) {
  return <details className="dev-context"><summary>What the model was given · costs, notebook and recent memory</summary>{present ? <><h5>Current costs</h5><ReadableValue value={context.current_costs} names={names} /><h5>Run notebook</h5><ReadableValue value={context.run_notebook?.entries ?? context.run_notebook} /><h5>Recent working memory</h5><ReadableValue value={context.working_memory} /></> : <p>No context event recorded for this request.</p>}</details>;
}

export function TechnicalDetails({ call, contextEvent, usage }: { call: TraceCall; contextEvent?: TraceEvent; usage: Data }) {
  return <details className="dev-technical"><summary>Technical details · raw JSON and IDs</summary><p className="dev-meta">{call.request_event.timestamp} · Request {call.request_id}</p>{call.tools.map((tool, index) => <div key={index}><JsonPanel label={`Tool arguments · ${tool.name || "unnamed"}`} value={tool.arguments} />{tool.arguments_parse_error && <JsonPanel label="Original argument string" value={tool.raw_arguments} />}{tool.delivered_results.map((result, resultIndex) => <JsonPanel key={resultIndex} label="Result sent back to model (matched call ID)" value={result} />)}</div>)}{call.journal_events.map((event) => <JsonPanel key={event.event_id} label={`Journal: ${title(event.type)} · event ${event.sequence}`} value={event} />)}<JsonPanel label="Full recorded provider request" value={call.request_event} />{call.response_event && <JsonPanel label="Full recorded provider response" value={call.response_event} />}{call.error_event && <JsonPanel label="Transport error" value={call.error_event} />}<JsonPanel label="Delivered context and helper exchanges" value={contextEvent?.payload} /><JsonPanel label="Token usage and cache accounting" value={usage} /></details>;
}

export { dollars };
