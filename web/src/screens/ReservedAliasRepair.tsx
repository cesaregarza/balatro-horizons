import { useRef, useState } from "react";
import { bootstrap, saveSettings } from "../api/client";
import { BUILT_IN_AGENTS, type ModelConfig } from "../modelSelection";

type Decision = { rename: string } | "remove";

// Older configs may name a model "heuristic", "random_legal" or "human". Those keys run as
// built-in players, so the picker skips them and settings refuse any save that still carries
// one. Each row records a decision; one save then repairs every reserved alias together.
export function ReservedAliasRepair({ config, setConfig, run, setNotice }: any) {
  const [names, setNames] = useState<Record<string, string>>({});
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const pending = useRef(false);
  const reserved = Object.entries((config.models ?? {}) as Record<string, ModelConfig>).filter(([key]) => BUILT_IN_AGENTS.includes(key));
  if (!reserved.length) return null;
  const decide = (key: string, decision: Decision) => setDecisions({ ...decisions, [key]: decision });

  async function repair() {
    const targets = reserved.flatMap(([key]) => { const decision = decisions[key]; return typeof decision === "object" ? [decision.rename] : []; });
    if (targets.some((name) => !name || name !== name.trim() || BUILT_IN_AGENTS.includes(name)))
      throw new Error("Choose new aliases without surrounding spaces that are not built-in player names.");
    if (new Set(targets).size !== targets.length) throw new Error("Each renamed alias needs a different new name.");
    const latest = (await bootstrap()).config;
    const latestReserved = Object.keys(latest.models).filter((key) => BUILT_IN_AGENTS.includes(key)).sort();
    if (latestReserved.join("\n") !== reserved.map(([key]) => key).sort().join("\n"))
      throw new Error("The reserved aliases changed since they were shown. Refresh the workbench and decide again.");
    const taken = targets.find((name) => name in latest.models);
    if (taken) throw new Error(`Alias ${taken} is already configured.`);
    const models = Object.fromEntries(Object.entries(latest.models).filter(([key]) => !BUILT_IN_AGENTS.includes(key)));
    for (const [key] of reserved) {
      const decision = decisions[key];
      if (typeof decision === "object") models[decision.rename] = latest.models[key];
    }
    setConfig(await saveSettings({ models, budgets: latest.budgets, skills: latest.skills }));
    setDecisions({});
    setNotice("Repaired the model aliases that used built-in player names.");
  }

  const ready = reserved.every(([key]) => decisions[key] !== undefined);
  return <section className="panel" aria-label="Aliases using built-in player names"><h2>Reserved model aliases</h2>
    <p className="muted">These model aliases share a name with a built-in player, so they are not offered for runs and settings cannot be saved until all of them are repaired together. Choose Rename or Remove for each, then Repair aliases. Past runs keep their recorded configuration.</p>
    <ul>{reserved.map(([key, model]) => { const decision = decisions[key]; return <li key={key}><b>{key}</b> · {model.provider} · <code>{model.model}</code>
      <label>New alias for {key}<input value={names[key] ?? ""} onChange={(e) => setNames({ ...names, [key]: e.target.value })} autoComplete="off" /></label>
      <button onClick={() => decide(key, { rename: names[key] ?? "" })}>Rename {key}</button>
      <button onClick={() => decide(key, "remove")}>Remove {key}</button>
      <span className="muted">{decision === undefined ? "No decision yet" : decision === "remove" ? "Will remove" : `Will rename to ${decision.rename}`}</span></li>; })}</ul>
    <button className="primary" disabled={!ready} onClick={() => {
      // Two clicks in one tick both see the button enabled; the ref admits only the first.
      if (pending.current || !ready) return;
      pending.current = true;
      void run(repair).finally(() => { pending.current = false; });
    }}>Repair aliases</button>
  </section>;
}
