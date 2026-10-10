import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { decisionDetail, downloadDecisionExport, listAnnotations, listDecisions, type DecisionExportFormat, type DecisionLedger, type DecisionRow, type View } from "../api/client";
import { harnessLabel, modelLabel } from "../modelSelection";
import { actionTitle, filters, humanize, matchesFilter } from "../decisionPresentation";
import { ModifierLegend } from "../ModifierLegend";
import { RunSpend } from "../RunSpend";
import { DecisionExplorerDetail } from "./DecisionExplorerDetail";
import { DecisionExplorerList } from "./DecisionExplorerList";
import { Annotate } from "./Annotate";
import { RestoreRun } from "../RestoreRun";
import { BudgetContinuation } from "../BudgetContinuation";
import "../decisions.css";
import "./decision-explorer/explorer-ux.css";

function recordedRows(data: DecisionLedger | null) {
  return [...(data?.actions || []), ...(data?.uncommitted_actions || []), ...(data?.pending_decisions || [])].sort((a, b) => a.decision - b.decision);
}
function inspectionError(caught: unknown) {
  return /TOKEN|SESSION|EXPIRED|UNAUTHORIZED|403|401/.test(String(caught))
    ? "This inspection session has expired. Copy any unsaved assessment, then reload this page to open a fresh session."
    : String(caught);
}

export function DecisionExplorer({ token, initialDecision, workbench = false, onRestored, onDecisionChange, onDirtyChange, active: screenActive = true }: {
  token: string; initialDecision?: number; workbench?: boolean; onRestored?: (episodeId: string) => void;
  onDecisionChange?: (decision: number) => void; onDirtyChange?: (dirty: boolean) => void; active?: boolean;
}) {
  const [ledger, setLedger] = useState<DecisionLedger | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [exportError, setExportError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [liveUpdates, setLiveUpdates] = useState(true);
  const [devMode, setDevMode] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [ante, setAnte] = useState("all");
  const [jump, setJump] = useState("");
  const [jumpError, setJumpError] = useState("");
  const [mobileDetail, setMobileDetail] = useState(initialDecision !== undefined);
  const [boardSide, setBoardSide] = useState<"before" | "after">("before");
  const [annotationView, setAnnotationView] = useState<View | null>(null);
  const [annotations, setAnnotations] = useState<any[]>([]);
  const [annotationDirty, setAnnotationDirty] = useState(false);
  const annotationPanel = useRef<HTMLElement>(null);
  const requestedDecision = useRef(initialDecision);
  const chosenDecision = useRef<number | undefined>(undefined);
  const focusedDecision = useRef("");
  const dirtyCallback = useRef(onDirtyChange);
  dirtyCallback.current = onDirtyChange;
  requestedDecision.current = initialDecision;
  useEffect(() => { dirtyCallback.current?.(annotationDirty); }, [annotationDirty]);
  useEffect(() => () => dirtyCallback.current?.(false), []);

  useEffect(() => {
    if (!screenActive) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = (delay: number) => { if (current && liveUpdates) timer = setTimeout(load, delay); };
    async function load() {
      if (!current) return;
      if (document.hidden) { schedule(2000); return; }
      setLoading(true);
      try {
        const data = await listDecisions(token);
        if (!current) return;
        setError(""); setLedger(data);
        const nextRows = recordedRows(data);
        setSelected((previous) => nextRows.find((row) => row.decision === previous)?.decision ?? nextRows.find((row) => row.decision === requestedDecision.current)?.decision ?? nextRows[0]?.decision ?? null);
        if (!data.summary?.outcome) schedule(2000);
      } catch (caught) { if (current) { setError(inspectionError(caught)); if (!/TOKEN|SESSION|EXPIRED|UNAUTHORIZED|403|401/.test(String(caught))) schedule(5000); } }
      finally { if (current) setLoading(false); }
    }
    void load();
    return () => { current = false; clearTimeout(timer); };
  }, [token, refresh, liveUpdates, screenActive]);

  useEffect(() => {
    if (initialDecision === undefined) return;
    if (initialDecision === chosenDecision.current) { chosenDecision.current = undefined; return; }
    setSelected(initialDecision); setMobileDetail(true);
  }, [token, initialDecision]);

  const rows = useMemo(() => recordedRows(ledger), [ledger]);
  const visible = rows.filter((row) => (ante === "all" || String(row.ante) === ante) && matchesFilter(row, filter) && [row.decision + 1, actionTitle(row), row.note, row.phase, row.blind, row.effects?.join(" "), row.cards?.join(" "), row.rejection_code].join(" ").toLowerCase().includes(search.trim().toLowerCase()));
  const active = rows.find((row) => row.decision === selected) || visible[0] || rows[0];
  const activeIndex = active ? visible.indexOf(active) : -1;
  const antes = [...new Set(rows.map((row) => row.ante))];
  const model = ledger?.manifest.config?.models?.[ledger.manifest.agent];
  const effort = model?.settings.reasoning_effort;
  const thinkingBudget = model?.settings.thinking_budget;
  const failed = ledger?.summary?.outcome && !["WIN", "GAME_LOSS"].includes(ledger.summary.outcome)
    ? [...rows].reverse().find((row) => !row.action_number || row.rejection_code) : undefined;
  const budgetStoppedRoot = Boolean(workbench && ledger && ["EPISODE_COST_CAP", "CAMPAIGN_COST_CAP", "EPISODE_AND_CAMPAIGN_COST_CAP"].includes(ledger.summary?.reason ?? "") && !ledger.manifest.parent_episode_id && !ledger.manifest.batch_id);

  useEffect(() => { setView(null); setBoardSide("before"); }, [token, active?.decision]);
  useEffect(() => {
    const key = `${token}:${view?.decision}`;
    if (!screenActive || !mobileDetail || !view || initialDecision === undefined ||
        focusedDecision.current === key || !window.matchMedia("(max-width: 1000px)").matches) return;
    focusedDecision.current = key;
    requestAnimationFrame(() => {
      const heading = document.querySelector<HTMLElement>("[data-decision-detail-heading]");
      heading?.scrollIntoView({ block: "start" }); heading?.focus({ preventScroll: true });
    });
  }, [token, view?.decision, initialDecision, mobileDetail, screenActive]);
  useEffect(() => {
    if (!active || !screenActive) return;
    let current = true;
    setDetailError("");
    decisionDetail(token, active.decision).then((data) => { if (current) setView(data); }).catch((caught) => { if (current) setDetailError(inspectionError(caught)); });
    return () => { current = false; };
  }, [token, active?.event_id, ledger?.source_journal_head, refresh, screenActive]);

  function closeAnnotation() {
    if (annotationDirty && !window.confirm("Discard the unsaved annotation draft?")) return false;
    setAnnotationView(null); setAnnotationDirty(false);
    return true;
  }
  function choose(row: DecisionRow) {
    if (annotationView && row.decision !== annotationView.decision && !closeAnnotation()) return false;
    setSelected(row.decision); setMobileDetail(true);
    chosenDecision.current = row.decision;
    if (onDecisionChange) onDecisionChange(row.decision);
    else if (ledger) window.history.replaceState(null, "", `#explore/${ledger.manifest.episode_id}/${row.decision}`);
    if (window.matchMedia("(max-width: 1000px)").matches) requestAnimationFrame(() => document.querySelector<HTMLElement>("[data-decision-detail-heading]")?.focus());
    return true;
  }
  function jumpTo(row: DecisionRow) { if (choose(row)) { setSearch(""); setFilter("all"); setAnte("all"); } }
  function goToDecision(event: FormEvent) {
    event.preventDefault();
    const decision = Number(jump);
    const row = rows.find((item) => item.decision === decision - 1);
    if (!Number.isInteger(decision) || decision < 1 || !row) { setJumpError("Enter the number of a recorded decision, starting at 1."); return; }
    setJumpError(""); jumpTo(row);
  }
  function backToChoices() {
    setMobileDetail(false);
    requestAnimationFrame(() => document.getElementById(`choice-${active?.event_id}`)?.focus());
  }
  async function annotate() {
    if (!active) return;
    if (annotationView?.decision === active.decision) {
      annotationPanel.current?.scrollIntoView({ block: "start" });
      annotationPanel.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
      return;
    }
    if (annotationView && !closeAnnotation()) return;
    setBusy(true); setDetailError("");
    try {
      const detail = view?.decision === active.decision ? view : await decisionDetail(token, active.decision);
      const revisions = await listAnnotations(token, detail.decision);
      setAnnotationView(detail); setAnnotations(revisions);
      requestAnimationFrame(() => {
        annotationPanel.current?.scrollIntoView({ block: "start" });
        annotationPanel.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
      });
    } catch (caught) { setDetailError(inspectionError(caught)); } finally { setBusy(false); }
  }
  async function runAnnotation(task: () => Promise<void>) { setBusy(true); try { await task(); } finally { setBusy(false); } }
  async function exportDecisions(format: DecisionExportFormat) {
    setExportError("");
    try { await downloadDecisionExport(token, format); } catch { setExportError("Could not create the download. Try again."); }
  }

  return <div className={`decision-explorer ${mobileDetail ? "detail-open" : ""}`}>
    <div className="review-top"><div><h1>Decision explorer</h1></div><button disabled={loading} onClick={() => setRefresh((n) => n + 1)}>Refresh decisions</button></div>
    <RunSpend spend={ledger?.spend} fallbackCost={ledger?.summary?.cost_usd} loading={!ledger && loading} status={error ? "Last recorded total · connection interrupted" : ledger?.summary?.outcome ? "Final recorded total" : !liveUpdates || !screenActive ? "Last recorded total · live updates paused" : "Live cost · refreshed every 2 seconds"} />
    {ledger && <div className="explorer-run-summary">
      <div className="explorer-identity"><strong>{ledger.manifest.config?.models?.[ledger.manifest.agent] ? modelLabel(ledger.manifest.config.models[ledger.manifest.agent], ledger.manifest.config.model_capabilities) : ledger.manifest.agent}</strong>{ledger.manifest.recorded_interface && <span>{harnessLabel(ledger.manifest.recorded_interface, ledger.manifest.current_harness)}</span>}<span>Context policy: {ledger.manifest.context_policy || "unknown"}</span><span>Provider wire policy: {ledger.manifest.provider_wire_policy || "unknown"}</span><span>{ledger.manifest.config?.deck} / {ledger.manifest.config?.stake}</span><code>{ledger.manifest.episode_id.slice(0, 10)}</code><span className="badge">{ledger.manifest.evidence_kind === "NATIVE" ? "Native run" : "Synthetic test"}</span>{!ledger.manifest.evaluation_eligible && <span>Not scored</span>}</div>
      <p className="explorer-progress"><strong>{humanize(ledger.summary?.outcome || "In progress")}</strong> · {ledger.actions.length} completed actions · {ledger.rounds?.filter((round) => round.cleared).length || 0} blinds cleared{effort ? ` · Reasoning effort: ${effort}` : thinkingBudget ? ` · Thinking budget: ${thinkingBudget} tokens` : ""}</p>
      {ledger.summary?.reason && <div className="explorer-failure"><p>Run stopped: {humanize(ledger.summary.reason)}.{failed && " The last attempt did not finish a game action."}</p>{failed && <button onClick={() => jumpTo(failed)}>View failed decision</button>}<details><summary>Technical stop code</summary><code>{ledger.summary.reason}</code></details></div>}
    </div>}
    {workbench && ledger && <div className="explorer-recovery">{budgetStoppedRoot ? <BudgetContinuation episodeId={ledger.manifest.episode_id} onRestored={(episodeId) => onRestored?.(episodeId)} /> : <RestoreRun episodeId={ledger.manifest.episode_id} outcome={ledger.summary?.outcome} autoPreview={Boolean(ledger.summary?.outcome)} onRestored={(episodeId) => onRestored?.(episodeId)} />}</div>}
    <div className="actions explorer-live-controls"><label className="check"><input type="checkbox" checked={liveUpdates} onChange={(e) => setLiveUpdates(e.target.checked)} />Update live</label><span role="status" aria-live="polite" className="muted">{ledger?.summary?.outcome ? "Run finished · all recorded decisions loaded" : !liveUpdates ? "Live updates paused" : error ? "Connection interrupted · retrying" : "Live · updates every 2 seconds"}</span><button disabled={!rows.length} onClick={() => jumpTo(rows[rows.length - 1])}>Jump to latest decision</button></div>
    {error && <p className="error" role="alert">{error}</p>}{!ledger && loading && <p role="status">Loading decisions…</p>}
    {ledger && <>
      <div className="ante-navigation" aria-label="Filter by ante"><button aria-pressed={ante === "all"} onClick={() => { setAnte("all"); setMobileDetail(false); }}>All antes</button>{antes.map((value) => <button key={String(value)} aria-pressed={ante === String(value)} onClick={() => { setAnte(String(value)); setMobileDetail(false); }}>{value == null ? "Unknown ante" : `Ante ${value}`}<span>{rows.filter((row) => row.ante === value).length}</span></button>)}</div>
      <div className="decision-toolbar"><label>Search decisions<input type="search" value={search} onChange={(e) => { setSearch(e.target.value); setMobileDetail(false); }} placeholder="Joker, card, or model note…" /></label><label>Action filter<select value={filter} onChange={(e) => { setFilter(e.target.value); setMobileDetail(false); }}>{filters.map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label><button onClick={() => { setSearch(""); setFilter("all"); setAnte("all"); setMobileDetail(false); }}>Clear filters</button></div>
      <form className="decision-jump" onSubmit={goToDecision}><label>Go to decision<input type="number" min={1} step={1} max={(rows[rows.length - 1]?.decision ?? 0) + 1} value={jump} onChange={(e) => setJump(e.target.value)} /></label><button disabled={!rows.length}>Go</button>{jumpError && <p className="error" role="alert">{jumpError}</p>}</form>
      <p className="muted" aria-live="polite">{visible.length} of {rows.length} recorded requests · Includes unfinished attempts. Numbers start at 1.</p>
      {!rows.length ? <div className="empty"><h2>No game actions recorded yet</h2><p>{liveUpdates ? "New decisions will appear here automatically." : "Resume live updates or refresh to load new decisions."}</p></div> : <>{!visible.length && <div className="empty"><h2>No matching decisions</h2><p>Try another term or clear the filters. The selected decision stays open.</p></div>}<div className="decision-workspace"><DecisionExplorerList rows={visible} active={active} onChoose={choose} />{active && <DecisionExplorerDetail token={token} active={active} view={view} activeIndex={activeIndex} visibleLength={visible.length} detailError={detailError} devMode={devMode && screenActive} liveUpdates={liveUpdates && screenActive} refresh={refresh} boardSide={boardSide} setBoardSide={setBoardSide} busy={busy} onBack={backToChoices} onPrevious={() => choose(visible[activeIndex - 1])} onNext={() => choose(visible[activeIndex + 1])} onAnnotate={annotate} />}</div></>}
      {annotationView && <section ref={annotationPanel} className="panel annotation-mode"><p className="badge">retrospective review</p>{active && active.decision !== annotationView.decision && <p className="notice">This assessment remains attached to decision #{annotationView.decision + 1} while you inspect decision #{active.decision + 1}.</p>}<button onClick={closeAnnotation}>Explore full run</button><Annotate key={annotationView.decision} token={token} view={annotationView} annotations={annotations} setAnnotations={setAnnotations} busy={busy} run={runAnnotation} onDirtyChange={setAnnotationDirty} /></section>}
      <details className="explorer-secondary"><summary>Exports, display guide and technical inspection</summary><p>Opening this view records retrospective exposure. Exports contain all server-projected decisions; filters do not limit downloads. Running episodes are labeled as partial snapshots.</p><div className="actions" aria-label="Decision exports"><button disabled={!rows.length} onClick={() => void exportDecisions("jsonl")}>Export JSONL</button><button onClick={() => void exportDecisions("json")}>Export JSON</button></div>{exportError && <p className="error" role="alert">{exportError}</p>}<ModifierLegend /><label className="dev-toggle"><input type="checkbox" checked={devMode} onChange={(event) => setDevMode(event.target.checked)} />Dev mode</label>{ledger.manifest.fixture && <p>Evaluator fixture: {ledger.manifest.fixture}</p>}</details>
    </>}
  </div>;
}
