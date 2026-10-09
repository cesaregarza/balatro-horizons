import { saveSettings } from "../api/client";
import { ModelConnection } from "./ModelConnection";

export function ModelsAndBudgets({ config, setConfig, run, setNotice, credentials, busy }: any) {
  return <>
    <h1>Models & budgets</h1><p>Direct provider access with a shared gameplay context. Keys are read from the backend environment.</p>
    <section className="panel"><h2>Game knowledge</h2><label>Skill access<select aria-label="Skill access" value={config.skills ?? "balatro-guide-v1"} onChange={(e) => setConfig({ ...config, skills: e.target.value })}><option value="balatro-guide-v1">Balatro guide — rules and strategy</option><option value="none">Native rules only</option></select></label><p className="muted">The model sees a small skill catalog and reads chapters when needed. Each run keeps its own frozen copy.</p><button onClick={() => run(async () => { setConfig(await saveSettings({ models: config.models, budgets: config.budgets, skills: config.skills ?? "balatro-guide-v1" })); setNotice("Skill access saved for new runs."); })}>Save skill access</button></section>
    <div className="split"><ModelConnection config={config} setConfig={setConfig} run={run} setNotice={setNotice} credentials={credentials} busy={busy} /><SpendingControls config={config} setConfig={setConfig} run={run} setNotice={setNotice} /></div>
  </>;
}

function SpendingControls({ config, setConfig, run, setNotice }: any) {
  return <section className="panel"><h2>Spending controls</h2><label className="check"><input type="checkbox" checked={config.budgets.paid_calls_enabled} onChange={(e) => setConfig({ ...config, budgets: { ...config.budgets, paid_calls_enabled: e.target.checked } })} /> Enable paid execution</label>{[["max_episode_cost_usd", "Episode ceiling ($)"], ["max_batch_cost_usd", "Batch ceiling ($)"]].map(([key, label]) => <label key={key}>{label}<input type="number" min="0.01" step="any" value={config.budgets[key] ?? ""} onChange={(e) => setConfig({ ...config, budgets: { ...config.budgets, [key]: e.target.value ? +e.target.value : null } })} /></label>)}<p className="muted">Every request reserves its maximum configured cost before being sent. Unknown usage retains the reservation.</p><button onClick={() => run(async () => { setConfig(await saveSettings({ models: config.models, budgets: config.budgets })); setNotice("Budget settings saved."); })}>Save spending limits</button><dl><dt>Committed actions</dt><dd>{config.budgets.max_game_actions}</dd><dt>Provider calls</dt><dd>{config.budgets.max_provider_calls}</dd><dt>Helpers per decision</dt><dd>{config.budgets.max_helper_calls_per_decision}</dd></dl></section>;
}
