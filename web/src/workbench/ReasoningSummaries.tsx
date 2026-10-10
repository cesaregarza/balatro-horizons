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
    .map((event) => record(record(event.payload).returned_reasoning));
  if (!responses.length) return null;
  return (
    <details>
      <summary>Returned reasoning summaries</summary>
      <p className="muted">
        Model-generated summaries may omit reasoning. Missing text does not show
        that the model failed to consider an option.
      </p>
      {responses.map((result, index) => {
        const summaries = (Array.isArray(result.texts) ? result.texts : [])
          .filter((text): text is string => typeof text === "string" && Boolean(text.trim()));
        const missing = result.status === "empty" ? "Returned summary text is empty."
          : result.status === "redacted" ? "Returned reasoning was redacted; no public summary text is available."
          : result.status === "unsupported" ? "Thinking text is not shown: the recorded model is older or its summary format is unverified."
          : "No summary returned.";
        return (
          <div key={index}>
            <h4>Response {index + 1}</h4>
            {summaries.length ? (
              summaries.map((text, i) => (
                <pre key={i}>{text}</pre>
              ))
            ) : (
              <p>{missing}</p>
            )}
            {summaries.length > 0 && result.redacted === true && <p>Additional reasoning was redacted.</p>}
          </div>
        );
      })}
    </details>
  );
}
