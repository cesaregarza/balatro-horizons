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

export type ModelCapability = {
  display_name: string;
  prompt_cache_diagnostics: boolean;
  explicit_cache_mode: boolean;
  supported_settings: string[];
  unsupported_settings: Record<string, (string | number)[]>;
  reasoning_efforts: string[];
};

export type CapabilityTable = {
  providers: Record<ModelConfig["provider"], { supported_settings: string[] }>;
  models: Record<string, ModelCapability>;
};

export const EMPTY_CAPABILITIES: CapabilityTable = {
  providers: { openai: { supported_settings: [] }, anthropic: { supported_settings: [] } },
  models: {},
};

export function harnessLabel(recordedInterface?: string | null, current = false) {
  return recordedInterface && !current
    ? `Legacy harness (${recordedInterface})`
    : "Current harness";
}

export function modelKey(model: Pick<ModelConfig, "provider" | "model">) {
  return `model:${model.provider}:${encodeURIComponent(model.model)}`;
}

function capabilityFor(model: ModelConfig, capabilities: CapabilityTable) {
  return capabilities.models[modelKey(model)];
}

export function modelLabel(model: ModelConfig, capabilities = EMPTY_CAPABILITIES) {
  const name = capabilityFor(model, capabilities)?.display_name ?? model.model;
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

export function effortOptions(model: ModelConfig, capabilities = EMPTY_CAPABILITIES) {
  return capabilityFor(model, capabilities)?.reasoning_efforts ?? [];
}

export function effortDefault(model: ModelConfig, capabilities = EMPTY_CAPABILITIES) {
  return String(
    model.settings.reasoning_effort ??
      (effortOptions(model, capabilities).includes("medium") ? "medium" : ""),
  );
}

export function supportsCachedHarness(model: ModelConfig, capabilities = EMPTY_CAPABILITIES) {
  const declared = capabilityFor(model, capabilities);
  return (
    model.provider === "openai" &&
    declared?.prompt_cache_diagnostics === true &&
    declared.explicit_cache_mode === true &&
    model.cached_input_usd_per_million != null &&
    model.cache_write_input_usd_per_million != null
  );
}

export function configureModel(
  model: ModelConfig,
  effort: string,
  capabilities = EMPTY_CAPABILITIES,
): ModelConfig {
  if (model.provider === "openai" && !supportsCachedHarness(model, capabilities))
    throw new Error(
      "The current harness requires a supported OpenAI model with cache read/write pricing configured.",
    );
  if (effort && !effortOptions(model, capabilities).includes(effort))
    throw new Error("Unsupported reasoning effort for this model.");
  return {
    ...model,
    settings: {
      ...supportedSettings(model.provider, model.settings, capabilities),
      ...(model.provider === "openai" && effort
        ? { reasoning_effort: effort }
        : {}),
    },
  };
}

export function supportedSettings(
  provider: ModelConfig["provider"],
  settings: Record<string, unknown>,
  capabilities = EMPTY_CAPABILITIES,
) {
  const allowed = new Set(capabilities.providers[provider]?.supported_settings ?? []);
  return Object.fromEntries(
    Object.entries(settings).filter(([key]) => allowed.has(key)),
  );
}
