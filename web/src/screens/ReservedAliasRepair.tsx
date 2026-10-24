import { useState } from "react";
import { bootstrap, saveSettings } from "../api/client";
import { BUILT_IN_AGENTS, type ModelConfig } from "../modelSelection";

// Older configs may name a model "heuristic", "random_legal" or "human". Those keys run as
// built-in players, so the picker skips them and settings refuse them until renamed or removed.
export function ReservedAliasRepair({ config, setConfig, run, setNotice }: any) {
  const [names, setNames] = useState<Record<string, string>>({});
  const reserved = Object.entries((config.models ?? {}) as Record<string, ModelConfig>).filter(([key]) => BUILT_IN_AGENTS.includes(key));
  if (!reserved.length) return null;

  async function replace(key: string, renamed?: string) {
    if (renamed !== undefined && (!renamed || renamed !== renamed.trim() || BUILT_IN_AGENTS.includes(renamed)))
      throw new Error("Choose a new alias without surrounding spaces that is not a built-in player name.");
    const latest = (await bootstrap()).config;
    const { [key]: record, ...models } = latest.models;
    if (!record) throw new Error(`Alias ${key} is no longer configured. Refresh the workbench.`);
    if (renamed !== undefined && renamed in latest.models) throw new Error(`Alias ${renamed} is already configured.`);
    const saved = await saveSettings({ models: renamed === undefined ? models : { ...models, [renamed]: record }, budgets: latest.budgets, skills: latest.skills });
    setConfig(saved);
    setNotice(renamed === undefined ? `Removed model alias ${key}.` : `Renamed model alias ${key} to ${renamed}.`);
  }

  return <section className="panel" aria-labelledby="reserved-aliases"><h2 id="reserved-aliases">Aliases using built-in player names</h2>
    <p className="muted">These model aliases share a name with a built-in player, so they are not offered for runs and settings cannot be saved until each is renamed or removed. Past runs keep their recorded configuration.</p>
    <ul>{reserved.map(([key, model]) => <li key={key}><b>{key}</b> · {model.provider} · <code>{model.model}</code>
      <label>New alias for {key}<input value={names[key] ?? ""} onChange={(e) => setNames({ ...names, [key]: e.target.value })} autoComplete="off" /></label>
      <button onClick={() => run(() => replace(key, names[key] ?? ""))}>Rename {key}</button>
      <button onClick={() => run(() => replace(key))}>Remove {key}</button></li>)}</ul>
  </section>;
}
