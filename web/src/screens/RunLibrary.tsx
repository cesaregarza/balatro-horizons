import { useEffect, useRef, useState } from "react";
import { bootstrap, listEpisodes, saveSettings, startRun, stopRun } from "../api/client";
import { ModelControls } from "../ModelControls";
import { RunSpend } from "../RunSpend";
import { CostOverrideControls, type CostOverride } from "../CostOverrideControls";
import { useRunAction } from "./useRunAction";
import { RuntimeConnection, useRuntimeConnection } from "./RuntimeConnection";
import { configureModel, EMPTY_CAPABILITIES, effortDefault, modelCatalog, modelKey, modelLabel, supportsCachedHarness } from "../modelSelection";
import { LibraryTable } from "./run-library/LibraryTable";
import { RunConfiguration, gameChoiceLabel } from "./run-library/RunConfiguration";
import "./run-library/library.css";

export function RunLibrary({ workbench, config, setConfig, episodes, setEpisodes, busy, run, openExplorer, openReview, onCompare, priorSeedExposure, setPriorSeedExposure, watch, setWatch, status, setNotice, onHumanStart, onRunStarted }: any) {
  const [agent, setAgent] = useState("heuristic");
  const [offline, setOffline] = useState(true);
  const [deck, setDeck] = useState(config.deck ?? "RED");
  const [stake, setStake] = useState(config.stake ?? "GOLD");
  const [seed, setSeed] = useState("");
  const [effort, setEffort] = useState("medium");
  const [costOverride, setCostOverride] = useState<CostOverride>(null);
  const startPending = useRef(false);
  const catalog = modelCatalog(config.models ?? {});
  const capabilities = config.model_capabilities ?? EMPTY_CAPABILITIES;
  const selectedModel = catalog[agent];
  const selectedModelSupported = !selectedModel || selectedModel.provider !== "openai" || supportsCachedHarness(selectedModel, capabilities);
  const action = useRunAction(run);
  const { connection, refresh: refreshConnection } = useRuntimeConnection(!offline);

  useEffect(() => { if (selectedModel) setEffort(effortDefault(selectedModel, capabilities)); }, [agent]);

  async function saveModelDefaults() {
    const latest = await bootstrap();
    const current = modelCatalog(latest.config.models)[agent];
    if (!current) throw new Error("Model is no longer configured. Refresh the workbench.");
    const chosen = configureModel(current, effort, latest.config.model_capabilities);
    const key = modelKey(chosen);
    const saved = await saveSettings({ models: { ...latest.config.models, [key]: chosen }, budgets: latest.config.budgets, skills: latest.config.skills });
    setConfig(saved);
  }

  async function refreshEpisodes() { setEpisodes(await listEpisodes()); }
  const budgets = config.budgets ?? {};
  const episodeLimit = costOverride === 10 ? 10 : costOverride === "uncapped" ? null : budgets.max_episode_cost_usd;
  const batchLimit = costOverride === "uncapped" ? null : costOverride ?? budgets.max_batch_cost_usd;

  return <>
    <header className="run-library-heading"><div><p className="eyebrow">BALATRO HORIZONS</p><h1>Runs and experiments</h1><p>Launch an episode, then explore its recorded decisions and outcomes.</p></div><button className="primary" onClick={() => { document.getElementById("start-a-run")?.scrollIntoView({ block: "start" }); document.querySelector<HTMLSelectElement>("#start-a-run select")?.focus({ preventScroll: true }); }}>Start a run</button></header>
    <LibraryTable config={config} workbench={workbench} episodes={episodes} busy={busy} openExplorer={openExplorer} openReview={openReview} onCompare={onCompare} priorSeedExposure={priorSeedExposure} setPriorSeedExposure={setPriorSeedExposure} />
    <div className="split">
      <section className="panel run-setup" id="start-a-run">
        <h2>Start a run</h2>
        <div className="form-row">
          <label>Model<select aria-label="Model" value={agent} onChange={(e) => { setAgent(e.target.value); setCostOverride(null); }} disabled={busy}>
            <optgroup label="Models">{Object.entries(catalog).map(([key, value]) => <option key={key} value={key}>{modelLabel(value, capabilities)}</option>)}</optgroup>
            <optgroup label="Baselines & human control"><option value="heuristic">Heuristic baseline</option><option value="random_legal">Random legal baseline</option>{workbench && <option value="human">Human player</option>}</optgroup>
          </select></label>
        </div>
        <RunConfiguration deck={deck} stake={stake} onDeck={setDeck} onStake={setStake} disabled={busy} />
        {selectedModel && <>
          <ModelControls model={selectedModel} effort={effort} onEffort={setEffort} capabilities={capabilities} disabled={busy} />
          <button disabled={busy || !selectedModelSupported} onClick={() => action(async () => { await saveModelDefaults(); setNotice("Model defaults saved. No run started."); })}>Save model defaults</button>
          <p className="muted">Run settings apply to this episode. Save model defaults only when you want future runs to use them.</p>
          <CostOverrideControls value={costOverride} onChange={setCostOverride} disabled={busy} />
        </>}
        <label>Private seed <span className="muted">optional</span><input value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="Generate an unseen seed" autoComplete="off" /></label>
        <label className="check"><input type="checkbox" checked={offline} onChange={(e) => setOffline(e.target.checked)} /> Synthetic test episode</label>
        <p className="muted">{offline ? "Synthetic test mode: exercises the application and is not a native game result." : "Real native game mode: requires passing environment and action-coverage gates."}</p>
        <dl className="launch-summary" aria-label="Launch summary">
          <dt>Mode</dt><dd>{offline ? "Synthetic test" : "Real native game"}</dd>
          <dt>Model / effort</dt><dd>{selectedModel ? `${modelLabel(selectedModel, capabilities)} / ${effort || "provider default"}` : `${agent} / not applicable`}</dd>
          <dt>Deck / stake</dt><dd>{gameChoiceLabel(deck)} Deck / {gameChoiceLabel(stake)} Stake</dd>
          <dt>Episode limit</dt><dd>{episodeLimit == null ? (costOverride === "uncapped" ? "Uncapped · this run only" : "Not configured") : `$${Number(episodeLimit).toFixed(2)}`}</dd>
          <dt>Batch limit</dt><dd>{batchLimit == null ? (costOverride === "uncapped" ? "Uncapped · this run only" : "Not configured") : `$${Number(batchLimit).toFixed(2)}`}</dd>
          <dt>Provider calls</dt><dd>{selectedModel ? budgets.paid_calls_enabled ? "Paid execution enabled" : "Paid execution disabled" : "No provider calls"}</dd>
        </dl>
        <div className="actions">
          <button className="primary" disabled={busy || startPending.current || !selectedModelSupported || (!offline && connection?.ready !== true)} onClick={() => {
            if (startPending.current || busy || !selectedModelSupported || (!offline && connection?.ready !== true)) return;
            startPending.current = true;
            void action(async () => {
              const chosen = selectedModel ? configureModel(selectedModel, effort, capabilities) : null;
              const result = await startRun({
                agent: chosen ? modelKey(chosen) : agent, ...(chosen ? { model_settings: chosen.settings } : {}),
                offline, deck, stake, seed: seed || null,
                ...(costOverride === null ? {} : { cost_override: costOverride }),
                ...(costOverride === "uncapped" ? { confirm_uncapped: true } : {}),
              });
              setCostOverride(null);
              setNotice("Run created: " + result.episode_id.slice(0, 10));
              await refreshEpisodes();
              if (agent === "human") onHumanStart(); else onRunStarted?.(result.episode_id);
            }).finally(() => { startPending.current = false; });
          }}>Start {offline ? "test episode" : "native run"} <span>↗</span></button>
          <button disabled={!status?.running} aria-label={status?.running ? "Stop active worker" : "Stop worker (idle)"} onClick={() => action(async () => { await stopRun(); setNotice("Stop requested. Any in-flight action will be recorded."); })}>Stop {status?.running ? "active worker" : "worker (idle)"}</button>
        </div>
        {!offline && <RuntimeConnection connection={connection} onRefresh={refreshConnection} />}
      </section>
      <section className="panel live">
        <p className="eyebrow">OPERATOR STATUS</p><h2>Live progress</h2>
        <p>Watching reveals the agent and its progress. That exposure is recorded before later review.</p>
        <button onClick={() => setWatch(!watch)}>{watch ? "Hide live status" : "Watch live status"}</button>
        {watch && status && <div><p className="status-line"><span className={"dot " + (status.running ? "" : "idle")} />{status.running ? "Worker running" : "Worker idle"}</p>{status.error && <p className="error">{status.error}</p>}{status.episodes.slice(0, 3).map((row: any) => <article className="live-run-card" key={row.episode_id}>
          <button onClick={() => openExplorer(row.episode_id)}><b>{config.models[row.agent] ? modelLabel(config.models[row.agent], capabilities) : row.agent}</b> · <code>{row.episode_id.slice(0, 10)}</code> · Explore decisions</button>
          <RunSpend compact spend={row.spend} fallbackCost={row.summary?.cost_usd ?? row.progress?.cost_usd} status={row.summary?.outcome ? "Final recorded total" : "Latest recorded total"} />
          <p>{row.summary?.outcome || row.progress?.phase || "In progress"} · {row.summary?.committed_actions ?? row.progress?.committed_actions ?? 0} actions</p>
        </article>)}</div>}
      </section>
    </div>
  </>;
}
