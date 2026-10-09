import { useState } from "react";
import { bootstrap, saveSettings } from "../api/client";
import { modelCatalog, modelKey, modelLabel, supportedSettings, type ModelConfig } from "../modelSelection";

type Draft = {
  provider: ModelConfig["provider"];
  model: string;
  inputRate: string;
  outputRate: string;
  cachedRate: string;
  writeRate: string;
  priceDate: string;
  settings: string;
};

function draftFor(value: ModelConfig): Draft {
  return {
    provider: value.provider, model: value.model,
    inputRate: String(value.input_usd_per_million), outputRate: String(value.output_usd_per_million),
    cachedRate: value.cached_input_usd_per_million == null ? "" : String(value.cached_input_usd_per_million),
    writeRate: value.cache_write_input_usd_per_million == null ? "" : String(value.cache_write_input_usd_per_million),
    priceDate: value.pricing_date, settings: JSON.stringify(value.settings),
  };
}

export function ModelConnection({ config, setConfig, run, setNotice, credentials, busy }: any) {
  const [draft, setDraft] = useState<Draft>({
    provider: "openai", model: "", inputRate: "", outputRate: "", cachedRate: "", writeRate: "",
    priceDate: new Date().toISOString().slice(0, 10), settings: "{}",
  });
  const presets: Record<string, ModelConfig> = config.model_presets ?? {};
  const entries = Object.entries(modelCatalog(config.models ?? {}));
  const update = (field: keyof Draft, value: string) => setDraft({ ...draft, [field]: value });
  const edit = (value: ModelConfig) => setDraft(draftFor(value));

  async function save() {
    const parsed = JSON.parse(draft.settings);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      throw new Error("Model settings must be a JSON object.");
    const latest = (await bootstrap()).config;
    const model = {
      provider: draft.provider, model: draft.model,
      input_usd_per_million: +draft.inputRate, output_usd_per_million: +draft.outputRate,
      cached_input_usd_per_million: draft.cachedRate === "" ? null : +draft.cachedRate,
      cache_write_input_usd_per_million: draft.writeRate === "" ? null : +draft.writeRate,
      pricing_date: draft.priceDate,
      settings: supportedSettings(draft.provider, parsed, latest.model_capabilities),
    };
    setConfig(await saveSettings({ models: { ...latest.models, [modelKey(model)]: model }, budgets: latest.budgets, skills: latest.skills }));
    setNotice("Model configuration saved.");
  }

  const cacheIncomplete = (draft.cachedRate === "") !== (draft.writeRate === "");
  const disabled = busy || !draft.model || draft.inputRate === "" || draft.outputRate === "" ||
    cacheIncomplete || (draft.provider === "openai" && draft.cachedRate === "");
  return <section className="panel">
    <h2>Model connection & pricing</h2>
    {Object.keys(presets).length > 0 && <>
      <label>Claude preset<select aria-label="Claude preset" value="" onChange={(e) => edit(presets[e.target.value])}>
        <option value="" disabled>Choose a Claude preset…</option>
        {Object.keys(presets).map((name) => <option key={name} value={name}>{name}</option>)}
      </select></label>
      <p className="muted">Prefills standard API pricing and adaptive thinking; nothing changes until you save. Review the pricing date. Haiku presets cover inputs up to 100k tokens.</p>
    </>}
    <form onSubmit={(e) => { e.preventDefault(); run(save); }}>
      <label>Provider<select aria-label="Provider" value={draft.provider} onChange={(e) => update("provider", e.target.value)}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select></label>
      <label>Exact model identifier<input value={draft.model} onChange={(e) => update("model", e.target.value)} placeholder="Provider model ID" /></label>
      <PriceFields draft={draft} update={update} />
      <label>Price verified on<input type="date" value={draft.priceDate} onChange={(e) => update("priceDate", e.target.value)} /></label>
      <p className="muted">{credentials[draft.provider] ? "Credential is available." : "Credential has not been configured."}</p>
      <label>Additional model settings (JSON)<textarea value={draft.settings} onChange={(e) => update("settings", e.target.value)} placeholder={'{"reasoning_effort":"medium"}'} /></label>
      <button disabled={disabled} type="submit">Save model</button>
    </form>
    <h3>Configured models</h3><p className="muted">Choose reasoning effort and save model defaults in Start a run.</p>
    {entries.map(([name, value]) => <p key={name}><b>{modelLabel(value, config.model_capabilities)}</b> · Current harness <button onClick={() => edit(value)}>Edit connection</button></p>)}
  </section>;
}

function PriceFields({ draft, update }: { draft: Draft; update: (field: keyof Draft, value: string) => void }) {
  const fields = [
    ["inputRate", "Input $ / million tokens"], ["outputRate", "Output $ / million tokens"],
    ["cachedRate", "Cache read $ / million tokens"], ["writeRate", "Cache write $ / million tokens"],
  ] as const;
  return <>
    <div className="field-grid">{fields.map(([key, label]) => <label key={key}>{label}<input type="number" min="0" step="any" value={draft[key]} onChange={(e) => update(key, e.target.value)} /></label>)}</div>
    <p className="muted">{draft.provider === "anthropic"
      ? "Claude: enter both cache rates to enable 5-minute prefix caching, or leave both blank. Use the 5-minute write price, not the 1-hour price."
      : "Requires GPT-5.6 or later. Enter separate read/write rates."}</p>
  </>;
}
