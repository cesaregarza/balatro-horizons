import { useEffect, useMemo, useState } from "react";
import { decisionDetail, downloadDecisionExport, listAnnotations, listDecisions, type DecisionExportFormat, type DecisionLedger, type DecisionRow, type View } from "../api/client";
import { harnessLabel, modelLabel } from "../modelSelection";
import { actionTitle, filters, humanize, matchesFilter } from "../decisionPresentation";
import { ModifierLegend } from "../ModifierLegend";
import { RunSpend } from "../RunSpend";
import { DecisionExplorerDetail } from "./DecisionExplorerDetail";
import { DecisionExplorerList } from "./DecisionExplorerList";
import { Annotate } from "./Annotate";
import "../decisions.css";

export function DecisionExplorer({ token, initialDecision }: { token: string; initialDecision?: number }) {
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
  const [mobileDetail, setMobileDetail] = useState(false);
  const [boardSide, setBoardSide] = useState<"before" | "after">("before");
  const [annotationView, setAnnotationView] = useState<View | null>(null);
  const [annotations, setAnnotations] = useState<any[]>([]);

  useEffect(() => {
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
        setError("");
        setLedger(data);
        const nextRows = [...data.actions, ...(data.uncommitted_actions || []), ...(devMode ? data.pending_decisions || [] : [])].sort((a, b) => a.decision - b.decision);
        setSelected((previous) => nextRows.find((row) => row.decision === previous)?.decision ?? nextRows.find((row) => row.decision === initialDecision)?.decision ?? nextRows[0]?.decision ?? null);
        if (!data.summary?.outcome) schedule(2000);
      } catch (caught) { if (current) { setError(String(caught)); schedule(5000); } }
      finally { if (current) setLoading(false); }
    }
    void load();
    return () => { current = false; clearTimeout(timer); };
  }, [token, refresh, initialDecision, liveUpdates, devMode]);

  const rows = useMemo(() => [...(ledger?.actions || []), ...(ledger?.uncommitted_actions || []), ...(devMode ? ledger?.pending_decisions || [] : [])].sort((a, b) => a.decision - b.decision), [ledger, devMode]);
  const visible = rows.filter((row) => (ante === "all" || String(row.ante) === ante) && matchesFilter(row, filter) && [row.decision + 1, actionTitle(row), row.note, row.phase, row.blind, row.effects?.join(" "), row.cards?.join(" "), row.rejection_code].join(" ").toLowerCase().includes(search.trim().toLowerCase()));
  const active = visible.find((row) => row.decision === selected) || visible[0];
  const activeIndex = active ? visible.indexOf(active) : -1;
  const antes = [...new Set(rows.map((row) => row.ante))];

  useEffect(() => { setView(null); setBoardSide("before"); }, [token, active?.decision]);
  useEffect(() => {
    if (!active) { setView(null); return; }
    let current = true;
    setDetailError("");
    decisionDetail(token, active.decision).then((data) => { if (current) setView(data); }).catch((caught) => { if (current) setDetailError(String(caught)); });
    return () => { current = false; };
  }, [token, active?.event_id, ledger?.source_journal_head, refresh]);

  function choose(row: DecisionRow) {
    setSelected(row.decision); setMobileDetail(true);
    if (ledger) window.history.replaceState(null, "", `#explore/${ledger.manifest.episode_id}/${row.decision}`);
    if (window.matchMedia("(max-width: 1000px)").matches) requestAnimationFrame(() => document.querySelector<HTMLElement>("[data-decision-detail-heading]")?.focus());
  }
  function backToChoices() {
    setMobileDetail(false);
    requestAnimationFrame(() => document.getElementById(`choice-${active?.event_id}`)?.focus());
  }
  async function annotate() {
    if (!active) return;
    setBusy(true); setDetailError("");
    try {
      const detail = await decisionDetail(token, active.decision);
      setAnnotationView(detail);
      setAnnotations(await listAnnotations(token, detail.decision));
    } catch (caught) { setDetailError(String(caught)); } finally { setBusy(false); }
  }
  function runAnnotation(task: () => Promise<void>) {
    return task();
  }
  async function exportDecisions(format: DecisionExportFormat) {
    setExportError("");
    try {
      await downloadDecisionExport(token, format);
    } catch { setExportError("Could not create the download. Try again."); }
  }

  return <div className={`decision-explorer ${mobileDetail ? "detail-open" : ""}`}>
    <div className="review-top"><div><p className="eyebrow">THE WHOLE RUN, AT A GLANCE</p><h1>Decision explorer</h1></div><button disabled={loading} onClick={() => setRefresh((n) => n + 1)}>Refresh decisions</button></div>
    <RunSpend spend={ledger?.spend} fallbackCost={ledger?.summary?.cost_usd} loading={!ledger && loading} status={error ? "Last recorded total · connection interrupted" : ledger?.summary?.outcome ? "Final recorded total" : !liveUpdates ? "Last recorded total · live updates paused" : "Live cost · refreshed every 2 seconds"} />
    <p className="explorer-intro">Browse recorded choices and outcomes, including new decisions during a run. Opening this view records retrospective exposure.</p>
    <div className="actions" aria-label="Decision exports"><label className="dev-toggle"><input type="checkbox" checked={devMode} onChange={(event) => setDevMode(event.target.checked)} />Dev mode</label><button disabled={!ledger || !rows.length} onClick={() => exportDecisions("jsonl")}>Export JSONL</button><button disabled={!ledger} onClick={() => exportDecisions("json")}>Export JSON</button></div>
    <ModifierLegend />
    <p className="muted">Download server-projected decision summaries, including choices, notes and recorded changes. Filters do not limit the export; a running episode is labeled as a partial snapshot.</p>
    {exportError && <p className="error" role="alert">{exportError}</p>}
    <div className="actions explorer-live-controls"><label className="check"><input type="checkbox" checked={liveUpdates} onChange={(e) => setLiveUpdates(e.target.checked)} />Update live</label><span role="status" aria-live="polite" className="muted">{ledger?.summary?.outcome ? "Run finished · all recorded decisions loaded" : !liveUpdates ? "Live updates paused" : error ? "Connection interrupted · retrying" : "Live · updates every 2 seconds"}</span><button disabled={!rows.length} onClick={() => { setSearch(""); setFilter("all"); setAnte("all"); choose(rows[rows.length - 1]); }}>Jump to latest decision</button></div>
    {error && <p className="error" role="alert">{error}</p>}{!ledger && loading && <p role="status">Loading decisions…</p>}
    {ledger && <>
      <div className="explorer-identity"><strong>{ledger.manifest.config?.models?.[ledger.manifest.agent] ? modelLabel(ledger.manifest.config.models[ledger.manifest.agent], ledger.manifest.config.model_capabilities) : ledger.manifest.agent}</strong>{ledger.manifest.recorded_interface && <span className="muted">{harnessLabel(ledger.manifest.recorded_interface, ledger.manifest.current_harness)}</span>}<span className={`badge ${ledger.manifest.evidence_kind === "NATIVE" ? "native" : "synthetic"}`}>{ledger.manifest.evidence_kind === "NATIVE" ? "Native run" : "Synthetic test"}</span><span>{ledger.manifest.config?.deck} / {ledger.manifest.config?.stake}</span><code>{ledger.manifest.episode_id.slice(0, 10)}</code>{!ledger.manifest.evaluation_eligible && <span className="muted">Not scored</span>}{ledger.manifest.fixture && <span className="badge">Evaluator fixture: {ledger.manifest.fixture}</span>}</div>
      <div className="explorer-stats"><div><small>Completed actions</small><strong>{ledger.actions.length}</strong></div><div><small>Blinds cleared</small><strong>{ledger.rounds?.filter((round) => round.cleared).length || 0}</strong></div><div><small>Blinds skipped</small><strong>{ledger.actions.filter((row) => row.type === "skip_blind").length}</strong></div><div><small>Recorded outcome</small><strong className="outcome">{humanize(ledger.summary?.outcome || "In progress")}</strong></div></div>
      {ledger.summary?.reason && <p className="explorer-stop">Run stopped: <code>{ledger.summary.reason}</code></p>}
      <div className="ante-navigation" aria-label="Filter by ante"><button aria-pressed={ante === "all"} onClick={() => { setAnte("all"); setMobileDetail(false); }}>All antes</button>{antes.map((value) => <button key={String(value)} aria-pressed={ante === String(value)} onClick={() => { setAnte(String(value)); setMobileDetail(false); }}>{value == null ? "Unknown ante" : `Ante ${value}`}<span>{rows.filter((row) => row.ante === value).length}</span></button>)}</div>
      <div className="decision-toolbar"><label>Search decisions<input type="search" value={search} onChange={(e) => { setSearch(e.target.value); setMobileDetail(false); }} placeholder="Joker, card, model note, or decision number…" /></label><label>Action filter<select value={filter} onChange={(e) => { setFilter(e.target.value); setMobileDetail(false); }}>{filters.map(([value, title]) => <option key={value} value={value}>{title}</option>)}</select></label><button onClick={() => { setSearch(""); setFilter("all"); setAnte("all"); setMobileDetail(false); }}>Clear filters</button></div>
      <p className="muted" aria-live="polite">{visible.length} of {rows.length} recorded requests · Includes uncommitted requests. Numbers start at 1.</p>
      {!rows.length ? <div className="empty"><h2>No game actions recorded yet</h2><p>{liveUpdates ? "New decisions will appear here automatically." : "Resume live updates or refresh to load new decisions."}</p></div> : !visible.length ? <div className="empty"><h2>No matching decisions</h2><p>Try another term or clear the filters.</p></div> : <div className="decision-workspace"><DecisionExplorerList rows={visible} active={active} onChoose={choose} />{active && <DecisionExplorerDetail token={token} active={active} view={view} activeIndex={activeIndex} visibleLength={visible.length} detailError={detailError} devMode={devMode} liveUpdates={liveUpdates} refresh={refresh} boardSide={boardSide} setBoardSide={setBoardSide} busy={busy} onBack={backToChoices} onPrevious={() => choose(visible[activeIndex - 1])} onNext={() => choose(visible[activeIndex + 1])} onAnnotate={annotate} />}</div>}
      {annotationView && <section className="panel annotation-mode"><p className="badge">retrospective review</p><button onClick={() => setAnnotationView(null)}>Explore full run</button><Annotate token={token} view={annotationView} annotations={annotations} setAnnotations={setAnnotations} busy={busy} run={runAnnotation} /></section>}
    </>}
  </div>;
}
