import { useEffect, useState } from "react";
import { bootstrap, compareBranch, humanStatus, listBatches, listEpisodes, listPanels, openExplorer, openReview, operatorStatus, setOperatorToken, setWorkbenchEnabled, type Episode, type View } from "./api/client";
import { DecisionExplorer } from "./screens/DecisionExplorer";
import { BatchesAndReports } from "./screens/BatchesAndReports";
import { ModelsAndBudgets } from "./screens/ModelsAndBudgets";
import { RunLibrary } from "./screens/RunLibrary";
import { Comparison } from "./workbench/Comparison";
import { HumanControl } from "./workbench/HumanControl";
import { Review } from "./workbench/Review";
import { usePolling } from "./usePolling";

type Tab = "runs" | "explore" | "review" | "human" | "batches" | "models" | "comparison";

export default function App() {
  const [tab, setTab] = useState<Tab>("runs");
  const [config, setConfig] = useState<any>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [panels, setPanels] = useState<any[]>([]);
  const [batches, setBatches] = useState<any[]>([]);
  const [review, setReview] = useState<{ token: string; view: View } | null>(null);
  const [explorer, setExplorer] = useState<{ token: string; initialDecision?: number } | null>(null);
  const [human, setHuman] = useState<any>(null);
  const [comparison, setComparison] = useState<any>(null);
  const [report, setReport] = useState<any>(null);
  const [credentials, setCredentials] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [watch, setWatch] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [priorSeedExposure, setPriorSeedExposure] = useState(false);

  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await task(); } catch (caught) { setError(String(caught)); } finally { setBusy(false); }
  }
  async function refresh() {
    setEpisodes(await listEpisodes());
    setPanels(await listPanels());
    setBatches(await listBatches());
  }
  async function openRun(episodeId: string, retrospective = false, decision?: number) {
    await run(async () => {
      if (retrospective) {
        const opened = await openExplorer({ episode_id: episodeId, retrospective: true, prior_seed_exposure: priorSeedExposure });
        setExplorer({ token: opened.review_token, initialDecision: decision });
        window.history.replaceState(null, "", `#explore/${episodeId}${decision == null ? "" : `/${decision}`}`);
        setTab("explore");
      } else {
        const opened = await openReview({ episode_id: episodeId, prior_seed_exposure: priorSeedExposure });
        setReview({ token: opened.review_token, view: opened.view });
        window.history.replaceState(null, "", window.location.pathname);
        setTab("review");
      }
    });
  }
  useEffect(() => {
    bootstrap().then(async (data) => {
      setOperatorToken(data.operator_token);
      setWorkbenchEnabled(data.workbench);
      setConfig(data.config);
      setCredentials(data.paid_credentials);
      setNotice(data.workbench ? "Workbench ready." : "Dashboard ready.");
      await refresh();
      const match = window.location.hash.match(/^#explore\/([a-f0-9]{32})(?:\/(\d+))?$/);
      if (match) await openRun(match[1], true, match[2] ? Number(match[2]) : undefined);
    }).catch((caught) => setError(String(caught)));
  }, []);
  usePolling(watch, 2000, operatorStatus, setStatus, (caught) => setError(String(caught)));
  usePolling(tab === "human", 1000, humanStatus, setHuman, (caught) => setError(String(caught)));

  if (!config) return <main className="loading"><h1>Balatro Horizons</h1><p>{error || "Opening the dashboard…"}</p></main>;
  const workbench = Boolean(config.workbench);
  const nav: [Tab, string][] = [["runs", "Runs"], ["explore", "Decision explorer"], ["batches", "Batches & reports"], ["models", "Models & budgets"], ...(workbench ? [["review", "Horizon review"], ["human", "Human control"]] as [Tab, string][] : [])];
  return <div className="shell">
    <aside><div className="brand"><span className="mark">♠</span><div><strong>BALATRO</strong><span>HORIZONS</span></div></div><p className="sidebar-label">{workbench ? "YOUR WORKBENCH" : "DASHBOARD"}</p><nav>{nav.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}</nav><div className="sidebar-bottom"><span className="dot" /> LOCAL WORKSPACE<p>Native decisions.<br />Inspectable continuations.</p><small>Red Deck · Gold Stake</small></div></aside>
    <main><header><span>{tab === "review" ? "EXPERT ANALYSIS" : "RUN WORKBENCH"}</span><div className="header-right"><span className="badge">1 worker</span><button onClick={() => run(() => refresh())}>Refresh</button></div></header>
      {error && <div role="alert" className="error">{error}</div>}{notice && <div role="status" className="notice">{notice}</div>}
      {tab === "runs" && <RunLibrary workbench={workbench} config={config} setConfig={setConfig} episodes={episodes} setEpisodes={setEpisodes} busy={busy} run={run} openExplorer={(id: string) => openRun(id, true)} openReview={(id: string) => openRun(id)} onCompare={(id: string) => run(async () => { setComparison(await compareBranch(id)); setTab("comparison"); })} priorSeedExposure={priorSeedExposure} setPriorSeedExposure={setPriorSeedExposure} watch={watch} setWatch={setWatch} status={status} setNotice={setNotice} onHumanStart={() => setTab("human")} />}
      {tab === "explore" && explorer && <DecisionExplorer key={explorer.token} token={explorer.token} initialDecision={explorer.initialDecision} />}
      {tab === "review" && review && <Review key={`${review.token}:${review.view.decision}`} token={review.token} initial={review.view} onExplore={(decision) => openRun(review.view.episode_id, true, decision)} onBranch={(id) => { setNotice("Branch created: " + id.slice(0, 10)); void refresh(); setTab("human"); }} />}
      {tab === "human" && <HumanControl human={human} setHuman={setHuman} run={run} />}
      {tab === "batches" && <BatchesAndReports config={config} setConfig={setConfig} panels={panels} setPanels={setPanels} batches={batches} setBatches={setBatches} report={report} setReport={setReport} offline={true} busy={busy} run={run} />}
      {tab === "models" && <ModelsAndBudgets config={config} setConfig={setConfig} run={run} setNotice={setNotice} credentials={credentials} busy={busy} />}
      {tab === "comparison" && comparison && <Comparison data={comparison} />}
      <footer>Balatro Horizons · Original runs remain immutable. Assisted branches are separate diagnostic evidence.</footer>
    </main>
  </div>;
}
