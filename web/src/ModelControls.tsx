import {
  effortOptions,
  supportsCachedHarness,
  type CapabilityTable,
  type ModelConfig,
} from "./modelSelection";

export function ModelControls({
  model,
  effort,
  onEffort,
  capabilities,
  disabled = false,
}: {
  model: ModelConfig;
  effort: string;
  onEffort: (effort: string) => void;
  capabilities: CapabilityTable;
  disabled?: boolean;
}) {
  const efforts = effortOptions(model, capabilities);
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
      </div>
      <p className="muted">
        The current harness loads details on demand and preserves provider
        reasoning across helper calls within a decision. Prompt caching is
        included for supported OpenAI models.
        {model.provider === "openai" &&
          !supportsCachedHarness(model, capabilities) &&
          " Configure a supported model and cache read/write prices in Settings before starting."}
      </p>
    </>
  );
}
