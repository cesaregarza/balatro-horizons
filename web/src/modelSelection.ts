export type ModelConfig = {
  provider: "openai" | "anthropic";
  model: string;
  input_usd_per_million: number;
  output_usd_per_million: number;
  cached_input_usd_per_million?: number | null;
  cache_write_input_usd_per_million?: number | null;
  pricing_date: string;
  settings: Record<string, string | number>;
};

export const CURRENT_HARNESS = "tools_v6";
export const harnesses = [[CURRENT_HARNESS, "Current harness (v6)"]] as const;

// Historical settings still need a stable preference order when aliases are
// deduplicated. This list does not define selectable harnesses.
const harnessOrder = [
  "operate_v1",
  "tools_v2",
  "tools_v3",
  "tools_v4",
  "tools_v5",
  CURRENT_HARNESS,
];

export function harnessLabel(model: ModelConfig) {
  const id = String(model.settings.harness_interface ?? "operate_v1");
  return id === CURRENT_HARNESS ? harnesses[0][1] : `Legacy harness (${id})`;
}

export function modelKey(model: Pick<ModelConfig, "provider" | "model">) {
  return `model:${model.provider}:${encodeURIComponent(model.model)}`;
}

export function modelLabel(model: ModelConfig) {
  const name = model.model.replace(
    /^gpt-(5\.6|6)-(luna|terra|sol|astra)$/,
    (_, generation: string, tier: string) =>
      `GPT-${generation} ${tier[0].toUpperCase()}${tier.slice(1)}`,
  );
  return `${name} · ${model.provider === "openai" ? "OpenAI" : "Anthropic"}`;
}

// Saved defaults win. Before defaults exist, prefer the latest configured
// harness for each exact provider/model pair. Keep historical aliases intact.
export function modelCatalog(models: Record<string, ModelConfig>) {
  const result: Record<string, ModelConfig> = {};
  const rank = (model: ModelConfig) =>
    harnessOrder.indexOf(
      String(model.settings.harness_interface ?? "operate_v1"),
    );
  for (const model of Object.values(models)) {
    const key = modelKey(model);
    if (models[key]) result[key] = models[key];
    else if (!result[key] || rank(model) > rank(result[key]))
      result[key] = model;
  }
  return result;
}

export function effortOptions(model: ModelConfig) {
  if (model.provider !== "openai") return [];
  if (/^gpt-5\.6(?:-|$)/.test(model.model))
    return ["none", "low", "medium", "high", "xhigh", "max"];
  if (/^gpt-6-astra(?:-|$)/.test(model.model))
    return ["low", "medium", "high", "xhigh", "max"];
  // Other models retain their pinned setting; don't invent compatibility.
  return model.settings.reasoning_effort
    ? [String(model.settings.reasoning_effort)]
    : [];
}

export function effortDefault(model: ModelConfig) {
  return String(
    model.settings.reasoning_effort ??
      (effortOptions(model).includes("medium") ? "medium" : ""),
  );
}

export function supportsCachedHarness(model: ModelConfig) {
  const version = /^gpt-(\d+)(?:\.(\d+))?(?:-|$)/.exec(model.model);
  return (
    model.provider === "openai" &&
    !!version &&
    (+version[1] > 5 || (+version[1] === 5 && +(version[2] ?? 0) >= 6)) &&
    model.cached_input_usd_per_million != null &&
    model.cache_write_input_usd_per_million != null
  );
}

export function configureModel(
  model: ModelConfig,
  effort: string,
  harness: string,
): ModelConfig {
  if (harness !== CURRENT_HARNESS)
    throw new Error(
      "Legacy harnesses are unavailable for new runs. Use the current harness.",
    );
  if (model.provider === "openai" && !supportsCachedHarness(model))
    throw new Error(
      "The current harness requires a supported OpenAI model with cache read/write pricing configured.",
    );
  if (effort && !effortOptions(model).includes(effort))
    throw new Error("Unsupported reasoning effort for this model.");
  return {
    ...model,
    settings: {
      ...model.settings,
      harness_interface: harness,
      ...(model.provider === "openai" && effort
        ? { reasoning_effort: effort }
        : {}),
    },
  };
}
