import { useState } from "react";
import { bootstrap, batchExport, batchReport, createBatch, createPanel, listBatches, listPanels, runBatch, saveSettings } from "../api/client";
import { configureModel, effortDefault, modelCatalog, modelLabel } from "../modelSelection";
import { download } from "../api/client";

export function BatchesAndReports({ config, setConfig, panels, setPanels, batches, setBatches, report, setReport, offline, busy, run }: any) {
  const catalog = modelCatalog(config.models ?? {});
  const [panel, setPanel] = useState("");
  const [agents, setAgents] = useState<string[]>(["heuristic", "random_legal"]);
  async function refresh() {
    setPanels(await listPanels());
    setBatches(await listBatches());
  }
  async function plan() {
    const latest = await bootstrap();
    const available = modelCatalog(latest.config.models);
    const chosen = { ...latest.config.models };
    for (const key of agents) {
      if (["heuristic", "random_legal"].includes(key)) continue;
      if (!available[key]) throw new Error("Model is no longer configured. Refresh the workbench.");
      chosen[key] = configureModel(available[key], effortDefault(available[key], latest.config.model_capabilities), latest.config.model_capabilities);
    }
    setConfig(await saveSettings({ models: chosen, budgets: latest.config.budgets, skills: latest.config.skills }));
    await createBatch({ panel_id: panel, agents, replicates: 2 });
    await refresh();
  }
  return <><h1>Batches & reports</h1><p>Freeze a shared seed panel and configuration before comparing agents.</p><section className="panel"><h2>Plan an exploratory batch</h2><button onClick={() => run(async () => { const created = await createPanel(20); setPanel(created.panel_id); await refresh(); })}>Generate 20 private development seeds</button><div className="form-row"><label>Seed panel<select value={panel} onChange={(e) => setPanel(e.target.value)}><option value="">Choose a panel</option>{panels.map((p: any) => <option key={p.panel_id} value={p.panel_id}>{p.panel_id.slice(0, 10)} · {p.count} seeds</option>)}</select></label><fieldset><legend>Models & players</legend>{[["heuristic", "Heuristic baseline"], ["random_legal", "Random legal baseline"], ...Object.entries(catalog).map(([key, value]) => [key, modelLabel(value, config.model_capabilities)])].map(([key, label]) => <label key={key} className="check"><input type="checkbox" aria-label={label} checked={agents.includes(key)} onChange={(e) => setAgents(e.target.checked ? [...agents, key] : agents.filter((value) => value !== key))} />{label}</label>)}<p className="muted">New plans use the current harness and each model’s saved effort. Existing frozen plans keep their original settings.</p></fieldset></div><button disabled={!panel || !agents.length || busy} onClick={() => run(plan)}>Freeze plan · 2 replicates</button></section>{batches.map((batch: any) => <section key={batch.batch_id} className="panel"><div className="area-title"><h3>Batch {batch.batch_id.slice(0, 10)}</h3><span>{batch.slots.length} planned slots</span></div><p>{batch.config.deck} / {batch.config.stake} · {batch.split}</p><div className="actions"><button onClick={() => run(async () => { await runBatch(batch.batch_id, offline); })}>Run {offline ? "synthetic test" : "native batch"}</button><button onClick={() => run(async () => setReport(await batchReport(batch.batch_id)))}>Reveal report</button><button onClick={() => run(async () => { const exported = await batchExport(batch.batch_id); await download(exported.download, "balatro-horizons-" + batch.batch_id + ".json"); })}>Export public results</button></div></section>)}{report && <BatchReport report={report} />}</>;
}

function BatchReport({ report }: any) {
  return <section className="panel"><h2>{report.evidence_kinds?.includes("SYNTHETIC_TEST") ? "Synthetic test outcomes" : "Autonomous outcomes"}</h2>{report.evidence_kinds?.includes("SYNTHETIC_TEST") && <p className="notice">Application accounting test only. These are not native benchmark results.</p>}<p>Valid outcomes include protocol failure, agent abort, and budget exhaustion. Coverage includes unresolved slots.</p><div className="table-wrap"><table><thead><tr><th>Agent</th><th>Wins / valid</th><th>Coverage</th><th>Missing-outcome bounds</th><th>All-attempt cost</th></tr></thead><tbody>{Object.entries(report.agents).map(([agent, value]: any) => <tr key={agent}><td>{agent}</td><td>{value.wins} / {value.valid}</td><td>{value.valid} / {value.planned}</td><td>{value.missing_outcome_bounds?.map((x: number) => (x * 100).toFixed(1) + "%").join(" – ")}</td><td>${value.all_attempt_cost_usd.toFixed(4)}</td></tr>)}</tbody></table></div><details><summary>Full accounting and paired comparisons</summary><pre>{JSON.stringify(report, null, 2)}</pre></details></section>;
}
