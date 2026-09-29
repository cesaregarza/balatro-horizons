import { useEffect, useRef, useState } from "react";
import { bootstrap, compareBranch, humanStatus, listBatches, listEpisodes, listPanels, openExplorer, openReview, operatorStatus, setOperatorToken, stopRun, type Episode, type View } from "./api/client";
import { readRoute, routeHash, routeKey, type DashboardRoute, type DashboardTab } from "./dashboardNavigation";
import { DecisionExplorer } from "./screens/DecisionExplorer";
import { BatchesAndReports } from "./screens/BatchesAndReports";
import { ModelsAndBudgets } from "./screens/ModelsAndBudgets";
import { RunLibrary } from "./screens/RunLibrary";
import { Comparison } from "./workbench/Comparison";
import { HumanControl } from "./workbench/HumanControl";
import { Review } from "./workbench/Review";
import { usePolling } from "./usePolling";

type ExplorerSession = { token: string; episodeId: string; initialDecision?: number };
type ReviewSession = { token: string; view: View };

export default function App() {
  const [tab, setTab] = useState<DashboardTab>("runs");
  const [config, setConfig] = useState<any>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [panels, setPanels] = useState<any[]>([]);
  const [batches, setBatches] = useState<any[]>([]);
  const [review, setReview] = useState<ReviewSession | null>(null);
  const [explorer, setExplorer] = useState<ExplorerSession | null>(null);
  const [human, setHuman] = useState<any>(null);
  const [comparison, setComparison] = useState<any>(null);
  const [report, setReport] = useState<any>(null);
  const [credentials, setCredentials] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [watch, setWatch] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [priorSeedExposure, setPriorSeedExposure] = useState(false);
  const sessions = useRef<{ explorer?: ExplorerSession; review?: ReviewSession; comparisonId?: string }>({});
  const currentRoute = useRef<DashboardRoute>({ tab: "runs" });
  const positions = useRef(new Map<string, number>());
  const appliedHash = useRef("");
  const navigationRequest = useRef(0);
  const explorerDirty = useRef(false);
  const reviewDirty = useRef(false);
  const priorExposure = useRef(priorSeedExposure);
  priorExposure.current = priorSeedExposure;
  const workbenchReady = useRef(false);

  async function run(task: () => Promise<void>) {
    setBusy(true); setError("");
    try { await task(); } catch (caught) { setError(String(caught)); } finally { setBusy(false); }
  }
  async function refresh() {
    const [nextEpisodes, nextPanels, nextBatches] = await Promise.all([listEpisodes(), listPanels(), listBatches()]);
    setEpisodes(nextEpisodes); setPanels(nextPanels); setBatches(nextBatches);
  }
  function restoreScroll(route: DashboardRoute) {
    requestAnimationFrame(() => window.scrollTo(0, positions.current.get(routeKey(route)) ?? 0));
  }
  async function applyRoute(route: DashboardRoute) {
    const request = ++navigationRequest.current;
    const previous = currentRoute.current;
    positions.current.set(routeKey(previous), window.scrollY);
    currentRoute.current = route;
    appliedHash.current = routeHash(route);
    setTab(route.tab); setError(""); setOpening(false);
    if (!workbenchReady.current && ["review", "human", "comparison"].includes(route.tab)) {
      setError("This workspace does not have research workbench controls enabled.");
      restoreScroll(route); return;
    }
    try {
      if (route.tab === "explore" && route.episodeId) {
        const old = sessions.current.explorer;
        if (old?.episodeId === route.episodeId) {
          const next = { ...old, initialDecision: route.decision ?? old.initialDecision };
          sessions.current.explorer = next; setExplorer(next);
        } else {
          setOpening(true);
          const opened = await openExplorer({ episode_id: route.episodeId, prior_seed_exposure: priorExposure.current });
          if (request !== navigationRequest.current) return;
          const next = { token: opened.review_token, episodeId: route.episodeId, initialDecision: route.decision };
          sessions.current.explorer = next; setExplorer(next); explorerDirty.current = false;
        }
      } else if (route.tab === "review" && route.episodeId) {
        if (sessions.current.review?.view.episode_id !== route.episodeId) {
          setOpening(true);
          const opened = await openReview({ episode_id: route.episodeId, prior_seed_exposure: priorExposure.current });
          if (request !== navigationRequest.current) return;
          sessions.current.review = { token: opened.review_token, view: opened.view };
          setReview(sessions.current.review); reviewDirty.current = false;
        }
      } else if (route.tab === "comparison" && route.episodeId && sessions.current.comparisonId !== route.episodeId) {
        setOpening(true);
        const result = await compareBranch(route.episodeId);
        if (request !== navigationRequest.current) return;
        sessions.current.comparisonId = route.episodeId; setComparison(result);
      }
      if (request === navigationRequest.current) restoreScroll(route);
    } catch (caught) {
      if (request === navigationRequest.current) setError(String(caught));
    } finally {
      if (request === navigationRequest.current) setOpening(false);
    }
  }
  function canNavigate(route: DashboardRoute) {
    const replacingExplorer = route.tab === "explore" && route.episodeId !== sessions.current.explorer?.episodeId && explorerDirty.current;
    const replacingReview = route.tab === "review" && route.episodeId !== sessions.current.review?.view.episode_id && reviewDirty.current;
    return !route.episodeId || !(replacingExplorer || replacingReview) || window.confirm("Discard the unsaved assessment before opening another run?");
  }
  function navigate(route: DashboardRoute, replace = false) {
    if (!canNavigate(route)) return;
    const hash = routeHash(route);
    if (window.location.hash !== hash) window.history[replace ? "replaceState" : "pushState"](null, "", hash);
    void applyRoute(route);
  }
  function openRun(episodeId: string, retrospective = false, decision?: number) {
    navigate({ tab: retrospective ? "explore" : "review", episodeId, decision });
  }
  function selectTab(next: DashboardTab) {
    const selected = sessions.current;
    navigate(next === "explore" && selected.explorer
      ? { tab: next, episodeId: selected.explorer.episodeId, decision: selected.explorer.initialDecision }
      : next === "review" && selected.review
        ? { tab: next, episodeId: selected.review.view.episode_id }
        : { tab: next });
  }
  function selectedDecision(decision: number) {
    const session = sessions.current.explorer;
    if (!session) return;
    const next = { ...session, initialDecision: decision };
    sessions.current.explorer = next; setExplorer(next);
    const route: DashboardRoute = { tab: "explore", episodeId: session.episodeId, decision };
    const hash = routeHash(route);
    currentRoute.current = route; appliedHash.current = hash;
    if (window.location.hash !== hash) window.history.pushState(null, "", hash);
  }
  async function restored(episodeId: string) {
    setWatch(true); setNotice("Continuation created. Watching its replay and progress.");
    navigate({ tab: "explore", episodeId });
    try { setEpisodes(await listEpisodes()); } catch (caught) { setError(String(caught)); }
  }
  useEffect(() => {
    let active = true;
    bootstrap().then(async (data) => {
      if (!active) return;
      setOperatorToken(data.operator_token);
      setConfig(data.config); setCredentials(data.paid_credentials);
      workbenchReady.current = Boolean(data.config.workbench);
      await refresh();
      if (active) await applyRoute(readRoute());
    }).catch((caught) => { if (active) setError(String(caught)); });
    const historyChanged = () => {
      if (window.location.hash === appliedHash.current) return;
      const route = readRoute();
      if (!canNavigate(route)) { window.history.pushState(null, "", appliedHash.current); return; }
      void applyRoute(route);
    };
    window.addEventListener("popstate", historyChanged);
    window.addEventListener("hashchange", historyChanged);
    return () => {
      active = false;
      window.removeEventListener("popstate", historyChanged);
      window.removeEventListener("hashchange", historyChanged);
    };
  }, []);
  usePolling(watch, 2000, operatorStatus, setStatus, (caught) => setError(String(caught)));
  usePolling(tab === "human", 1000, humanStatus, setHuman, (caught) => setError(String(caught)));

  if (!config) return <main className="loading"><h1>Balatro Horizons</h1><p>{error || "Opening the dashboard…"}</p></main>;
  const workbench = Boolean(config.workbench);
  const explorerSelected = Boolean(explorer && currentRoute.current.episodeId === explorer.episodeId);
  const reviewSelected = Boolean(review && currentRoute.current.episodeId === review.view.episode_id);
  const nav: [DashboardTab, string][] = [["runs", "Runs"], ["explore", "Decision explorer"], ["batches", "Batches & reports"], ["models", "Models & budgets"], ...(workbench ? [["review", "Horizon review"], ["human", "Human control"]] as [DashboardTab, string][] : [])];
  return <div className="shell">
    <aside><div className="brand"><span className="mark">♠</span><div><strong>BALATRO</strong><span>HORIZONS</span></div></div><p className="sidebar-label">{workbench ? "YOUR WORKBENCH" : "DASHBOARD"}</p><nav>{nav.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => selectTab(id)}>{label}</button>)}</nav><div className="sidebar-bottom"><span className="dot" /> LOCAL WORKSPACE<p>Native decisions.<br />Inspectable continuations.</p><small>Public run evidence</small></div></aside>
    <main><header><span>{tab === "review" ? "EXPERT ANALYSIS" : "RUN WORKBENCH"}</span><div className="header-right"><span className="badge">{!watch ? "Worker status hidden" : !status ? "Checking worker…" : status.running ? "Worker running" : "Worker idle"}</span><button title="Shows model identity and progress; records operator exposure" onClick={() => setWatch(!watch)}>{watch ? "Hide worker status" : "Show worker status"}</button>{watch && status?.active_episode && <button onClick={() => openRun(status.active_episode, true)}>Open active run</button>}{watch && status?.running && <button disabled={busy} onClick={() => run(async () => { await stopRun(); setNotice("Stop requested. Any in-flight action will be recorded."); })}>Stop active run</button>}<button onClick={() => run(refresh)}>Refresh</button></div></header>
      {error && <div role="alert" className="error">{error}</div>}{notice && <div className="notice"><span role="status">{notice}</span><button className="dismiss-notice" aria-label="Dismiss notification" onClick={() => setNotice("")}>×</button></div>}
      {opening && <p role="status">Opening the recorded run…</p>}
      <section hidden={tab !== "runs"} aria-label="Runs workspace"><RunLibrary workbench={workbench} config={config} setConfig={setConfig} episodes={episodes} setEpisodes={setEpisodes} busy={busy || opening} run={run} openExplorer={(id: string) => openRun(id, true)} openReview={(id: string) => openRun(id)} onCompare={(id: string) => navigate({ tab: "comparison", episodeId: id })} priorSeedExposure={priorSeedExposure} setPriorSeedExposure={setPriorSeedExposure} watch={watch} setWatch={setWatch} status={status} setNotice={setNotice} onHumanStart={() => selectTab("human")} onRunStarted={(id: string) => { setWatch(true); openRun(id, true); }} /></section>
      <section hidden={tab !== "explore" || opening} aria-label="Explorer workspace">{explorer && <div hidden={!explorerSelected}><DecisionExplorer key={explorer.token} token={explorer.token} initialDecision={explorer.initialDecision} active={tab === "explore" && explorerSelected && !opening} onDecisionChange={selectedDecision} onDirtyChange={(dirty) => { explorerDirty.current = dirty; }} workbench={workbench} onRestored={(id) => void restored(id)} /></div>}{!explorerSelected && <ChooseRun onChoose={() => selectTab("runs")} title="Choose a run to explore" />}</section>
      <section hidden={tab !== "review" || opening} aria-label="Research review workspace">{workbench && <>{review && <div hidden={!reviewSelected}><Review key={review.token} token={review.token} initial={review.view} onDirtyChange={(dirty) => { reviewDirty.current = dirty; }} onExplore={(decision) => openRun(review.view.episode_id, true, decision)} onBranch={(id) => { setNotice("Branch created: " + id.slice(0, 10)); void refresh(); selectTab("human"); }} /></div>}{!reviewSelected && <ChooseRun onChoose={() => selectTab("runs")} title="Choose a run for staged review" />}</>}</section>
      {workbench && <section hidden={tab !== "human"}><HumanControl human={human} setHuman={setHuman} run={run} /></section>}
      <section hidden={tab !== "batches"}><BatchesAndReports config={config} setConfig={setConfig} panels={panels} setPanels={setPanels} batches={batches} setBatches={setBatches} report={report} setReport={setReport} offline={true} busy={busy} run={run} /></section>
      <section hidden={tab !== "models"}><ModelsAndBudgets config={config} setConfig={setConfig} run={run} setNotice={setNotice} credentials={credentials} busy={busy} /></section>
      {tab === "comparison" && !opening && (comparison && sessions.current.comparisonId === currentRoute.current.episodeId ? <Comparison data={comparison} onExplore={(id: string) => openRun(id, true)} onBack={() => selectTab("runs")} /> : <ChooseRun onChoose={() => selectTab("runs")} title="Choose runs to compare" />)}
      <footer>Balatro Horizons · Original runs remain immutable. Assisted branches are separate diagnostic evidence.</footer>
    </main>
  </div>;
}

function ChooseRun({ title, onChoose }: { title: string; onChoose: () => void }) {
  return <section className="empty"><h1>{title}</h1><p>Open the run library and choose Explore decisions or Review. No run has been selected yet.</p><button className="primary" onClick={onChoose}>Open run library</button></section>;
}
