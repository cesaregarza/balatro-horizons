import type { View } from "../api/client";

type RecordValue = Record<string, unknown>;
function record(value: unknown): RecordValue {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : {};
}

export function ReasoningSummaries({
  events,
}: {
  events: NonNullable<View["action_events"]>;
}) {
  const responses = events
    .filter((event) => event.type === "provider_response")
    .map((event) => record(record(event.payload).body))
    .filter((body) => Array.isArray(body.output));
  if (!responses.length) return null;
  return (
    <details>
      <summary>Returned reasoning summaries</summary>
      <p className="muted">
        Model-generated summaries may omit reasoning. Missing text does not show
        that the model failed to consider an option.
      </p>
      {responses.map((body, index) => {
        const summaries = (body.output as unknown[])
          .map(record)
          .filter(
            (item) => item.type === "reasoning" && Array.isArray(item.summary),
          )
          .flatMap((item) => item.summary as unknown[])
          .map(record)
          .filter(
            (part) =>
              part.type === "summary_text" && typeof part.text === "string",
          );
        return (
          <div key={index}>
            <h4>Response {index + 1}</h4>
            {summaries.length ? (
              summaries.map((part, i) => (
                <pre key={i}>{part.text as string}</pre>
              ))
            ) : (
              <p>No summary returned.</p>
            )}
          </div>
        );
      })}
    </details>
  );
}
