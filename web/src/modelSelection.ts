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

export function harnessLabel(recordedInterface?: string | null, current = false) {
  return recordedInterface && !current
    ? `Legacy harness (${recordedInterface})`
    : "Current harness";
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

// Saved defaults win; otherwise retain the first configured exact model pair.
export function modelCatalog(models: Record<string, ModelConfig>) {
  const result: Record<string, ModelConfig> = {};
  for (const model of Object.values(models)) {
    const key = modelKey(model);
    if (models[key]) result[key] = models[key];
    else if (!result[key]) result[key] = model;
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
): ModelConfig {
  if (model.provider === "openai" && !supportsCachedHarness(model))
    throw new Error(
      "The current harness requires a supported OpenAI model with cache read/write pricing configured.",
    );
  if (effort && !effortOptions(model).includes(effort))
    throw new Error("Unsupported reasoning effort for this model.");
  return {
    ...model,
    settings: {
      ...supportedSettings(model.provider, model.settings),
      ...(model.provider === "openai" && effort
        ? { reasoning_effort: effort }
        : {}),
    },
  };
}

export function supportedSettings(
  provider: ModelConfig["provider"],
  settings: Record<string, unknown>,
) {
  const allowed =
    provider === "openai"
      ? new Set(["temperature", "reasoning_effort", "reasoning_summary"])
      : new Set(["temperature", "thinking_budget"]);
  return Object.fromEntries(
    Object.entries(settings).filter(([key]) => allowed.has(key)),
  );
}
