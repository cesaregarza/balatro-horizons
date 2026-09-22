import { useState } from "react";
import { bootstrap, saveSettings } from "../api/client";
import {
  modelCatalog,
  modelKey,
  modelLabel,
  supportedSettings,
  type ModelConfig,
} from "../modelSelection";

export function ModelsAndBudgets({ config, setConfig, run, setNotice, credentials, busy }: any) {
  const catalog = modelCatalog(config.models ?? {});
  const capabilities = config.model_capabilities;
  const [provider, setProvider] = useState<ModelConfig["provider"]>("openai");
  const [model, setModel] = useState("");
  const [inputRate, setInputRate] = useState("");
  const [outputRate, setOutputRate] = useState("");
  const [cachedRate, setCachedRate] = useState("");
  const [writeRate, setWriteRate] = useState("");
  const [priceDate, setPriceDate] = useState(new Date().toISOString().slice(0, 10));
  const [modelSettings, setModelSettings] = useState("{}");

  async function saveModel() {
    const latest = (await bootstrap()).config;
    const key = modelKey({ provider, model });
    const parsed = JSON.parse(modelSettings);
    const models = {
      ...latest.models,
      [key]: {
        provider,
        model,
        input_usd_per_million: +inputRate,
        output_usd_per_million: +outputRate,
        ...(provider === "openai" ? { cached_input_usd_per_million: +cachedRate, cache_write_input_usd_per_million: +writeRate } : {}),
        pricing_date: priceDate,
        settings: supportedSettings(provider, { ...(latest.models[key]?.settings ?? {}), ...parsed }, capabilities),
      },
    };
    setConfig(await saveSettings({ models, budgets: latest.budgets, skills: latest.skills }));
    setNotice("Model configuration saved.");
  }

  function edit(value: ModelConfig) {
    setProvider(value.provider);
    setModel(value.model);
    setInputRate(String(value.input_usd_per_million));
    setOutputRate(String(value.output_usd_per_million));
    setCachedRate(value.cached_input_usd_per_million == null ? "" : String(value.cached_input_usd_per_million));
    setWriteRate(value.cache_write_input_usd_per_million == null ? "" : String(value.cache_write_input_usd_per_million));
    setPriceDate(value.pricing_date);
    setModelSettings(JSON.stringify(value.settings));
  }

  return <>
    <h1>Models & budgets</h1><p>Direct provider access with a shared gameplay context. Keys are read from the backend environment.</p>
    <section className="panel"><h2>Game knowledge</h2><label>Skill access<select aria-label="Skill access" value={config.skills ?? "balatro-guide-v1"} onChange={(e) => setConfig({ ...config, skills: e.target.value })}><option value="balatro-guide-v1">Balatro guide — rules and strategy</option><option value="none">Native rules only</option></select></label><p className="muted">The model sees a small skill catalog and reads chapters when needed. Each run keeps its own frozen copy.</p><button onClick={() => run(async () => { setConfig(await saveSettings({ models: config.models, budgets: config.budgets, skills: config.skills ?? "balatro-guide-v1" })); setNotice("Skill access saved for new runs."); })}>Save skill access</button></section>
    <div className="split"><ModelConnection provider={provider} setProvider={setProvider} model={model} setModel={setModel} inputRate={inputRate} setInputRate={setInputRate} outputRate={outputRate} setOutputRate={setOutputRate} cachedRate={cachedRate} setCachedRate={setCachedRate} writeRate={writeRate} setWriteRate={setWriteRate} priceDate={priceDate} setPriceDate={setPriceDate} modelSettings={modelSettings} setModelSettings={setModelSettings} saveModel={saveModel} catalog={catalog} edit={edit} credentials={credentials} busy={busy} capabilities={capabilities} /><SpendingControls config={config} setConfig={setConfig} run={run} setNotice={setNotice} /></div>
  </>;
}

function ModelConnection(props: any) {
  const { provider, setProvider, model, setModel, inputRate, setInputRate, outputRate, setOutputRate, cachedRate, setCachedRate, writeRate, setWriteRate, priceDate, setPriceDate, modelSettings, setModelSettings, saveModel, catalog, edit, credentials, busy } = props;
  const entries = Object.entries(catalog) as [string, ModelConfig][];
  return <section className="panel"><h2>Model connection & pricing</h2><div className="form-row"><label>Provider<select value={provider} onChange={(e) => setProvider(e.target.value)}><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option></select></label></div><label>Exact model identifier<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="Provider model ID" /></label><div className="form-row"><label>Input $ / million tokens<input type="number" min="0" step="any" value={inputRate} onChange={(e) => setInputRate(e.target.value)} /></label><label>Output $ / million tokens<input type="number" min="0" step="any" value={outputRate} onChange={(e) => setOutputRate(e.target.value)} /></label></div>{provider === "openai" && <div className="field-grid"><label>Cache read $ / million tokens<input type="number" min="0" step="any" value={cachedRate} onChange={(e) => setCachedRate(e.target.value)} /></label><label>Cache write $ / million tokens<input type="number" min="0" step="any" value={writeRate} onChange={(e) => setWriteRate(e.target.value)} /></label><p className="muted">Requires GPT-5.6 or later. Enter separate read/write rates.</p></div>}<label>Price verified on<input type="date" value={priceDate} onChange={(e) => setPriceDate(e.target.value)} /></label><p className="muted">{credentials[provider] ? "Credential is available." : "Credential has not been configured."}</p><label>Additional model settings (JSON)<textarea value={modelSettings} onChange={(e) => setModelSettings(e.target.value)} placeholder={'{"reasoning_effort":"medium","reasoning_summary":"auto"}'} /></label><button disabled={busy || !model || inputRate === "" || outputRate === "" || (provider === "openai" && (cachedRate === "" || writeRate === ""))} onClick={() => saveModel()}>Save model</button><h3>Configured models</h3><p className="muted">Choose reasoning effort and save model defaults in Start a run.</p>{entries.map(([name, value]) => <p key={name}><b>{modelLabel(value, props.capabilities)}</b> · Current harness <button onClick={() => edit(value)}>Edit connection</button></p>)}</section>;
}

function SpendingControls({ config, setConfig, run, setNotice }: any) {
  return <section className="panel"><h2>Spending controls</h2><label className="check"><input type="checkbox" checked={config.budgets.paid_calls_enabled} onChange={(e) => setConfig({ ...config, budgets: { ...config.budgets, paid_calls_enabled: e.target.checked } })} /> Enable paid execution</label>{[["max_episode_cost_usd", "Episode ceiling ($)"], ["max_batch_cost_usd", "Batch ceiling ($)"]].map(([key, label]) => <label key={key}>{label}<input type="number" min="0.01" step="any" value={config.budgets[key] ?? ""} onChange={(e) => setConfig({ ...config, budgets: { ...config.budgets, [key]: e.target.value ? +e.target.value : null } })} /></label>)}<p className="muted">Every request reserves its maximum configured cost before being sent. Unknown usage retains the reservation.</p><button onClick={() => run(async () => { setConfig(await saveSettings({ models: config.models, budgets: config.budgets })); setNotice("Budget settings saved."); })}>Save spending limits</button><dl><dt>Committed actions</dt><dd>{config.budgets.max_game_actions}</dd><dt>Provider calls</dt><dd>{config.budgets.max_provider_calls}</dd><dt>Helpers per decision</dt><dd>{config.budgets.max_helper_calls_per_decision}</dd></dl></section>;
}
