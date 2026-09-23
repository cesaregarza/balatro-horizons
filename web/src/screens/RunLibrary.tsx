import { useEffect, useState } from "react";
import { bootstrap, listEpisodes, saveSettings, startRun, stopRun, type Episode } from "../api/client";
import { ModelControls } from "../ModelControls";
import { RunSpend } from "../RunSpend";
import { usePolling } from "../usePolling";
import { useRunAction } from "./useRunAction";
import { RuntimeConnection, useRuntimeConnection } from "./RuntimeConnection";
import {
  configureModel,
  EMPTY_CAPABILITIES,
  effortDefault,
  modelCatalog,
  modelKey,
  modelLabel,
  supportsCachedHarness,
} from "../modelSelection";

export function RunLibrary({
  workbench,
  config,
  setConfig,
  episodes,
  setEpisodes,
  busy,
  run,
  openExplorer,
  openReview,
  onCompare,
  priorSeedExposure,
  setPriorSeedExposure,
  watch,
  setWatch,
  status,
  setNotice,
  onHumanStart,
}: any) {
  const [agent, setAgent] = useState("heuristic");
  const [offline, setOffline] = useState(true);
  const [preset, setPreset] = useState("pilot");
  const [seed, setSeed] = useState("");
  const [effort, setEffort] = useState("medium");
  const catalog = modelCatalog(config.models ?? {});
  const capabilities = config.model_capabilities ?? EMPTY_CAPABILITIES;
  const selectedModel = catalog[agent];
  const selectedModelSupported = !selectedModel || selectedModel.provider !== "openai" || supportsCachedHarness(selectedModel, capabilities);
  const action = useRunAction(run);
  const { connection, refresh: refreshConnection } = useRuntimeConnection(!offline);

  useEffect(() => {
    if (selectedModel) setEffort(effortDefault(selectedModel, capabilities));
  }, [agent]);

  async function selectAgent(value: string) {
    setAgent(value);
    if (catalog[value]) setEffort(effortDefault(catalog[value], capabilities));
  }

  async function saveModelDefaults() {
    const latest = await bootstrap();
    const current = modelCatalog(latest.config.models)[agent];
    if (!current) throw new Error("Model is no longer configured. Refresh the workbench.");
    const chosen = configureModel(current, effort, latest.config.model_capabilities);
    const key = modelKey(chosen);
    const saved = await saveSettings({ models: { ...latest.config.models, [key]: chosen }, budgets: latest.config.budgets, skills: latest.config.skills });
    setConfig(saved);
    return key;
  }

  async function refreshEpisodes() {
    setEpisodes(await listEpisodes());
  }

  return (
    <>
      <div className="hero">
        <p className="eyebrow">FULL RUNS. LONGER HORIZONS.</p>
        <h1>Every choice leaves<br />a future to inspect.</h1>
        <p>Run an agent through Balatro, then examine the tradeoffs<br className="desktop" /> between scoring now, surviving next, and building ahead.</p>
      </div>
      <div className="split">
        <section className="panel run-setup">
          <h2>Start a run</h2>
          <div className="form-row">
            <label>Model<select aria-label="Model" value={agent} onChange={(e) => selectAgent(e.target.value)} disabled={busy}>
              <optgroup label="Models">{Object.entries(catalog).map(([key, value]) => <option key={key} value={key}>{modelLabel(value, capabilities)}</option>)}</optgroup>
              <optgroup label="Baselines & human control"><option value="heuristic">Heuristic baseline</option><option value="random_legal">Random legal baseline</option>{workbench && <option value="human">Human player</option>}</optgroup>
            </select></label>
            <label>Game configuration<select value={preset} onChange={(e) => setPreset(e.target.value)}><option value="pilot">Red / Gold · strategic pilot</option><option value="smoke">Red / White · smoke</option></select></label>
          </div>
          {selectedModel && <>
            <ModelControls model={selectedModel} effort={effort} onEffort={setEffort} capabilities={capabilities} disabled={busy} />
            <button disabled={busy || !selectedModelSupported} onClick={() => action(async () => { await saveModelDefaults(); setNotice("Model defaults saved. No run started."); })}>Save model defaults</button>
            <p className="muted">Starting a run also saves these defaults. Each run keeps its exact settings.</p>
          </>}
          <label>Private seed <span className="muted">optional</span><input value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="Generate an unseen seed" autoComplete="off" /></label>
          <label className="check"><input type="checkbox" checked={offline} onChange={(e) => setOffline(e.target.checked)} /> Synthetic pipeline test</label>
          <p className="muted">{offline ? "Synthetic episodes test the application and are labeled throughout." : "Native autonomous runs require passing environment and action-coverage gates."}</p>
          <div className="actions">
            <button className="primary" disabled={busy || !selectedModelSupported || (!offline && connection?.ready !== true)} onClick={() => action(async () => {
              const chosenAgent = selectedModel ? await saveModelDefaults() : agent;
              const result = await startRun({ agent: chosenAgent, offline, preset, seed: seed || null });
              setNotice("Run created: " + result.episode_id.slice(0, 10));
              await refreshEpisodes();
              if (agent === "human") onHumanStart();
            })}>Start {offline ? "test episode" : "native run"} <span>↗</span></button>
            <button onClick={() => action(async () => { await stopRun(); setNotice("Stop requested. Any in-flight action will be recorded."); })}>Stop worker</button>
          </div>
          {!offline && <RuntimeConnection connection={connection} onRefresh={refreshConnection} />}
        </section>
        <section className="panel live">
          <p className="eyebrow">OPERATOR VIEW</p><h2>Live progress</h2>
          <p>Watching reveals the agent and its progress. That exposure is recorded before later review.</p>
          <button onClick={() => setWatch(!watch)}>{watch ? "Hide live status" : "Watch live status"}</button>
          {watch && status && <div><p className="status-line"><span className={"dot " + (status.running ? "" : "idle")} />{status.running ? "Worker running" : "Worker idle"}</p>{status.error && <p className="error">{status.error}</p>}{status.episodes.slice(0, 3).map((row: any) => <div key={row.episode_id}>
            <p><b>{config.models[row.agent] ? modelLabel(config.models[row.agent], capabilities) : row.agent}</b> · <code>{row.episode_id.slice(0, 10)}</code></p>
            <RunSpend compact spend={row.spend} fallbackCost={row.summary?.cost_usd ?? row.progress?.cost_usd} status={row.summary?.outcome ? "Final recorded total" : "Latest recorded total"} />
            <p>{row.summary?.outcome || row.progress?.phase || "In progress"} · {row.summary?.committed_actions ?? row.progress?.committed_actions ?? 0} actions</p>
          </div>)}</div>}
        </section>
      </div>
      <RunTable workbench={workbench} episodes={episodes} busy={busy} openExplorer={openExplorer} openReview={openReview} onCompare={onCompare} priorSeedExposure={priorSeedExposure} setPriorSeedExposure={setPriorSeedExposure} />
    </>
  );
}

function RunTable({ workbench, episodes, busy, openExplorer, openReview, onCompare, priorSeedExposure, setPriorSeedExposure }: any) {
  return <section className="panel"><div className="area-title"><h2>Run library</h2><span>{episodes.length} recorded episodes</span></div><p className="muted">Explore decisions to see the whole run, including its outcome. {workbench && "Review → opens staged prospective review. "}Model names and outcomes stay hidden in this list.</p>{workbench && <label className="check"><input type="checkbox" checked={priorSeedExposure} onChange={(e) => setPriorSeedExposure(e.target.checked)} /> I have previously played or watched the seed of the run I am about to review.</label>}{episodes.length ? <div className="table-wrap"><table><thead><tr><th>Episode</th><th>Configuration</th><th>Evidence</th><th>Created</th><th /></tr></thead><tbody>{episodes.map((episode: Episode) => <tr key={episode.episode_id}><td><code>{episode.episode_id.slice(0, 10)}</code>{episode.branch && <span className="badge">branch</span>}</td><td>{episode.deck} / {episode.stake}</td><td><span className={"badge " + (episode.evidence_kind === "NATIVE" ? "native" : "synthetic")}>{episode.evidence_kind === "NATIVE" ? "Native" : "Synthetic test"}</span></td><td>{new Date(episode.created_at).toLocaleString()}{episode.fixture && <small> · evaluator fixture</small>}{!episode.evaluation_eligible && <small> · not scored</small>}</td><td><button className="primary" disabled={busy} onClick={() => openExplorer(episode.episode_id)}>Explore decisions</button>{workbench && <><button onClick={() => openReview(episode.episode_id)}>Review →</button>{episode.branch && <button onClick={() => onCompare(episode.episode_id)}>Compare outcomes</button>}</>}</td></tr>)}</tbody></table></div> : <div className="empty"><span>♧</span><h3>Your first run starts here.</h3><p>Record an episode to inspect its decisions and future branches.</p></div>}</section>;
}
