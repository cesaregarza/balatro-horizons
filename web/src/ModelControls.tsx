import {
  effortOptions,
  harnesses,
  supportsCachedHarness,
  type ModelConfig,
} from "./modelSelection";

export function ModelControls({
  model,
  effort,
  harness,
  onEffort,
  onHarness,
  disabled = false,
}: {
  model: ModelConfig;
  effort: string;
  harness: string;
  onEffort: (effort: string) => void;
  onHarness: (harness: string) => void;
  disabled?: boolean;
}) {
  const efforts = effortOptions(model);
  return (
    <>
      <div className="form-row">
        <label>
          Reasoning effort
          <select
            aria-label="Reasoning effort"
            value={effort}
            onChange={(e) => onEffort(e.target.value)}
            disabled={disabled || !efforts.length}
          >
            {!efforts.length && (
              <option value="">Provider default / pinned settings</option>
            )}
            {efforts.map((value) => (
              <option key={value} value={value}>
                {value === "xhigh"
                  ? "Extra high"
                  : value[0].toUpperCase() + value.slice(1)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Harness
          <select
            aria-label="Harness"
            value={harness}
            onChange={(e) => onHarness(e.target.value)}
            disabled={disabled}
          >
            {harnesses.map(([id, label]) => (
              <option
                key={id}
                value={id}
                disabled={
                  (id === "tools_v4" ||
                    (id === "tools_v5" && model.provider === "openai")) &&
                  !supportsCachedHarness(model)
                }
              >
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        Focused tools load details on demand. Provider continuation preserves
        reasoning across helper calls within one decision. All modes use tools
        to submit moves.
        {model.provider === "openai" && !supportsCachedHarness(model) &&
          " The cached harness requires a supported OpenAI model with cache pricing configured."}
      </p>
    </>
  );
}
