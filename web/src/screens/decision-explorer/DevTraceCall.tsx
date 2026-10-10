import { objectNames, recordedModelContext, record, statusText, toolTitle, type ObjectNames, type TraceCall, type TraceEvent } from "../../devTracePresentation";
import { DeliveredContext, JournalResult, RequestedTool, TechnicalDetails, dollars } from "./DevTraceSupport";
import { ReadableValue } from "./DevTraceValues";

export function CallDetails({ call, index, contextEvent, names: canonicalNames }: { call: TraceCall; index: number; contextEvent?: TraceEvent; names: ObjectNames }) {
  // Tool arguments address the delivered public view, not the backend ID namespace.
  const delivered = recordedModelContext(call.request_event.payload.body);
  const context = delivered ?? record(contextEvent?.payload.context);
  const names = new Map([...canonicalNames, ...objectNames(context.observation)]);
  const usage = record(call.response_event?.payload.reported_usage);
  return <li className="dev-call"><h4>Call {index + 1} · {call.tools.map((tool) => toolTitle(tool, names)).join(" · ") || "No tool call returned"}</h4><p className="dev-status">{statusText(call.status)}</p><p className="dev-meta">Transport attempt {call.request_event.payload.attempt ?? "unknown"} · {call.response_event ? `API cost ${dollars(call.response_event.payload.cost_usd)}` : `Reserved ${dollars(call.request_event.payload.reserved_usd)}; final usage not recorded`}{call.response_event && <ReportedTokens usage={usage} />}</p>{call.error_event && <div className="dev-result dev-error"><strong>Provider error</strong><ReadableValue value={call.error_event.payload} /></div>}{call.tools.map((tool, ordinal) => <RequestedTool key={`${tool.call_id}-${ordinal}`} tool={tool} ordinal={ordinal} context={context} names={names} />)}{call.journal_events.some((event) => event.type !== "agent_operation") && <div className="dev-results"><h5>Recorded by the harness</h5><p className="dev-meta">Associated with this request by journal order. Multiple returned tools may all be rejected.</p>{call.journal_events.map((event) => <JournalResult key={event.event_id} event={event} names={names} />)}</div>}{!call.tools.length && !call.error_event && <p>{call.response_event ? "The provider returned no tool calls. Its full response is available in technical details." : "No model tool call has been recorded for this request."}</p>}<DeliveredContext context={context} present={Boolean(contextEvent)} names={names} /><TechnicalDetails call={call} contextEvent={contextEvent} usage={usage} /></li>;
}

function ReportedTokens({ usage }: { usage: Record<string, unknown> }) {
  const count = (value: unknown) => typeof value === "number" ? value.toLocaleString() : "unknown";
  return <> · Input tokens: {count(usage.input_tokens)} · Output tokens: {count(usage.output_tokens)}
    {usage.cache_read_tokens != null && ` · Cache read: ${count(usage.cache_read_tokens)}`}
    {usage.cache_write_tokens != null && ` · Cache write: ${count(usage.cache_write_tokens)}`}</>;
}
