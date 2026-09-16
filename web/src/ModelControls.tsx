import {
  effortOptions,
  CURRENT_HARNESS,
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
                  model.provider === "openai" && !supportsCachedHarness(model)
                }
              >
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        The current harness loads details on demand and preserves provider
        reasoning across helper calls within a decision. Prompt caching is
        included for supported OpenAI models.
        {model.settings.harness_interface !== CURRENT_HARNESS &&
          " This model has a legacy saved default; new runs use the current harness."}
        {model.provider === "openai" &&
          !supportsCachedHarness(model) &&
          " Configure a supported model and cache read/write prices in Settings before starting."}
      </p>
    </>
  );
}
