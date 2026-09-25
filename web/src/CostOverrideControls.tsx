import "./costOverride.css";

export type CostOverride = 10 | "uncapped" | null;

type Props = {
  value: CostOverride;
  onChange: (value: CostOverride) => void;
  disabled?: boolean;
  defaultLabel?: string;
  tenDollarLabel?: string;
  showCurrent?: boolean;
  legend?: string;
  description?: string;
};

export function CostOverrideControls({
  value,
  onChange,
  disabled = false,
  defaultLabel = "Current limits",
  tenDollarLabel = "$10 total",
  showCurrent = true,
  legend = "Cost for this new run",
  description = "Applies to this new run only. Saved spending limits and defaults stay unchanged.",
}: Props) {
  function selectUncapped() {
    const accepted = window.confirm(
      "Are you sure? This run will have no dollar ceiling. Spending can keep growing. Call and action limits still apply.",
    );
    if (accepted) onChange("uncapped");
  }

  return (
    <fieldset className="cost-override" disabled={disabled}>
      <legend>{legend}</legend>
      <div className="cost-override-options">
        {showCurrent && (
          <button type="button" aria-pressed={value === null} onClick={() => onChange(null)}>
            {defaultLabel}
          </button>
        )}
        <button type="button" aria-pressed={value === 10} onClick={() => onChange(10)}>
          {tenDollarLabel}
        </button>
        <button
          type="button"
          className="cost-override-uncapped"
          aria-pressed={value === "uncapped"}
          onClick={selectUncapped}
        >
          Uncapped
        </button>
      </div>
      <p>{description}</p>
    </fieldset>
  );
}
