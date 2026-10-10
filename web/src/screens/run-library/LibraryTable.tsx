import { useEffect, useMemo, useState } from "react";
import { listOperatorEpisodes, type Episode, type OperatorEpisode } from "../../api/client";
import { modelCatalog, modelLabel, EMPTY_CAPABILITIES } from "../../modelSelection";

type Props = { config: any; workbench: boolean; episodes: Episode[]; busy: boolean; openExplorer: (id: string) => void; openReview: (id: string) => void; onCompare: (id: string) => void; priorSeedExposure: boolean; setPriorSeedExposure: (value: boolean) => void };
const PAGE_SIZE = 15;
const money = (value: number | null) => value == null ? "Unknown" : `$${value.toFixed(4)}`;

export function LibraryTable(props: Props) {
  const [mode, setMode] = useState<"blinded" | "operator">("blinded");
  const [operatorRows, setOperatorRows] = useState<OperatorEpisode[]>([]);
  const [loadingOperatorRows, setLoadingOperatorRows] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const [model, setModel] = useState("all");
  const [evidence, setEvidence] = useState("all");
  const [status, setStatus] = useState("all");
  const [deck, setDeck] = useState("all");
  const [stake, setStake] = useState("all");
  const [sort, setSort] = useState("newest");
  const [includeTests, setIncludeTests] = useState(true);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => { setPage(0); setSelected([]); }, [mode, props.episodes]);

  useEffect(() => {
    if (mode !== "operator") return;
    let active = true;
    setLoadingOperatorRows(true);
    listOperatorEpisodes().then((rows) => { if (active) { setOperatorRows(rows); setLoadError(""); } }).catch((error) => { if (active) setLoadError(error instanceof Error ? error.message : "Could not load operator episode details."); }).finally(() => { if (active) setLoadingOperatorRows(false); });
    return () => { active = false; };
  }, [mode, props.episodes]);

  const source = mode === "operator" ? operatorRows : props.episodes;
  const catalog = modelCatalog(props.config.models ?? {});
  const capabilities = props.config.model_capabilities ?? EMPTY_CAPABILITIES;
  const displayModel = (row: OperatorEpisode) => {
    const known = Object.values(catalog).find((candidate) => candidate.model === row.model_name);
    return known ? modelLabel(known, capabilities) : row.model_name || "Unknown model";
  };
  const rows = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    return source.filter((row) => {
      const op = row as OperatorEpisode;
      if (!includeTests && (row.evidence_kind !== "NATIVE" || row.fixture)) return false;
      if (evidence === "EVALUATOR_FIXTURE" ? !row.fixture : evidence !== "all" && row.evidence_kind !== evidence) return false;
      if (mode === "operator" && status !== "all" && (op.outcome ? "complete" : "unfinished") !== status) return false;
      if (mode === "operator" && model !== "all" && op.model_name !== model) return false;
      if (deck !== "all" && row.deck !== deck) return false;
      if (stake !== "all" && row.stake !== stake) return false;
      if (!q) return true;
      return [row.episode_id, row.deck, row.stake, row.evidence_kind, op.model_name, displayModel(op), op.reasoning_effort, op.recorded_interface, op.outcome, op.reason].some((value) => String(value ?? "").toLocaleLowerCase().includes(q));
    }).sort((a, b) => sort === "oldest" ? a.created_at.localeCompare(b.created_at) : sort === "deck" ? a.deck.localeCompare(b.deck) || a.stake.localeCompare(b.stake) : b.created_at.localeCompare(a.created_at));
  }, [source, includeTests, evidence, status, deck, stake, search, sort, mode, model, catalog, capabilities]);

  const byId = new Map(source.map((row) => [row.episode_id, row as OperatorEpisode]));
  function rootFor(row: OperatorEpisode): OperatorEpisode {
    let current = row;
    const visited = new Set<string>();
    while (current.parent_episode_id && byId.has(current.parent_episode_id) && !visited.has(current.episode_id)) {
      visited.add(current.episode_id);
      current = byId.get(current.parent_episode_id)!;
    }
    return current;
  }
  const groups = mode === "operator" ? (() => {
    const grouped = new Map<string, OperatorEpisode[]>();
    for (const row of rows as OperatorEpisode[]) {
      const root = rootFor(row);
      const group = grouped.get(root.episode_id) ?? [root];
      if (!group.some((item) => item.episode_id === row.episode_id)) group.push(row);
      grouped.set(root.episode_id, group);
    }
    return [...grouped.values()].map((group) => [group[0], ...group.slice(1).sort((a, b) => a.created_at.localeCompare(b.created_at))]);
  })() : rows.map((row) => [row as OperatorEpisode]);
  const pages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE));
  const visibleGroups = groups.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selectedRows = mode === "operator" ? operatorRows.filter((row) => selected.includes(row.episode_id)) : [];
  useEffect(() => { if (page >= pages) setPage(Math.max(0, pages - 1)); }, [page, pages]);

  return <section className="panel run-library-panel">
    <div className="area-title"><h2>Run library</h2><span>{rows.length} matching episodes</span></div>
    <div className="library-mode" role="group" aria-label="Library information view">
      <button aria-pressed={mode === "blinded"} onClick={() => setMode("blinded")}>Blinded review</button>
      <button aria-pressed={mode === "operator"} onClick={() => setMode("operator")}>Operator details</button>
    </div>
    <p className="muted">{mode === "blinded" ? "Model and outcome details stay hidden. Switching to Operator details reveals both and is recorded as operator exposure." : "Operator details reveal model identity, outcome, reason, and attempt-only spend."}</p>
    {props.workbench && <label className="check"><input type="checkbox" checked={props.priorSeedExposure} onChange={(e) => props.setPriorSeedExposure(e.target.checked)} /> I have previously played or watched the seed of the run I am about to review.</label>}
    <div className="library-filters">
      <label>Search<input aria-label="Search runs" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder={mode === "operator" ? "ID, model, effort, outcome…" : "ID, deck, stake…"} /></label>
      <label>Evidence<select aria-label="Evidence filter" value={evidence} onChange={(e) => { setEvidence(e.target.value); setPage(0); }}><option value="all">All evidence</option><option value="NATIVE">Native</option><option value="SYNTHETIC_TEST">Synthetic test</option><option value="EVALUATOR_FIXTURE">Evaluator fixture</option></select></label>
      {mode === "operator" && <label>Status<select aria-label="Status filter" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}><option value="all">Any status</option><option value="complete">Outcome recorded</option><option value="unfinished">No outcome recorded</option></select></label>}
      {mode === "operator" && <label>Model<select aria-label="Model filter" value={model} onChange={(e) => { setModel(e.target.value); setPage(0); }}><option value="all">All models</option>{Array.from(new Set(operatorRows.map((row) => row.model_name))).sort().map((value) => <option key={value || "unknown"} value={value}>{displayModel(operatorRows.find((row) => row.model_name === value)!)}</option>)}</select></label>}
      <label>Deck<select aria-label="Deck filter" value={deck} onChange={(e) => { setDeck(e.target.value); setPage(0); }}><option value="all">All decks</option>{Array.from(new Set(source.map((row) => row.deck))).sort().map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Stake<select aria-label="Stake filter" value={stake} onChange={(e) => { setStake(e.target.value); setPage(0); }}><option value="all">All stakes</option>{Array.from(new Set(source.map((row) => row.stake))).sort().map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Sort<select aria-label="Sort runs" value={sort} onChange={(e) => { setSort(e.target.value); setPage(0); }}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="deck">Deck and stake</option></select></label>
      <label className="check library-hide-tests"><input type="checkbox" checked={includeTests} onChange={(e) => { setIncludeTests(e.target.checked); setPage(0); }} /> Show test and fixture data</label>
    </div>
    {mode === "operator" && <p className="muted">Select Compare on two run cards to compare their records. {selected.length} of 2 selected.</p>}
    {selectedRows.length === 2 && <LocalComparison rows={selectedRows} displayModel={displayModel} onClose={() => setSelected([])} />}
    {loadError && mode === "operator" && <p className="error" role="alert">{loadError}</p>}
    {loadingOperatorRows && mode === "operator" ? <p role="status">Loading operator episode details…</p> : !rows.length ? <div className="empty"><h3>No runs match these filters.</h3><p>Adjust search or filters to see other episodes.</p></div> : <>
      <div className="library-cards">{visibleGroups.map((group) => <div className="episode-group" key={group[0].episode_id}>{group.map((row) => <EpisodeCard key={row.episode_id} row={row} displayModel={displayModel(row)} mode={mode} workbench={props.workbench} busy={props.busy} selected={selected.includes(row.episode_id)} onSelect={() => setSelected((current) => current.includes(row.episode_id) ? current.filter((id) => id !== row.episode_id) : current.length < 2 ? [...current, row.episode_id] : current)} onExplore={props.openExplorer} onReview={props.openReview} onCompare={props.onCompare} />)}</div>)}</div>
      <nav className="library-pagination" aria-label="Run pages"><button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} of {pages}</span><button disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button></nav>
    </>}
  </section>;
}

function EpisodeCard({ row, displayModel, mode, workbench, busy, selected, onSelect, onExplore, onReview, onCompare }: any) {
  return <article className={`episode-card${row.parent_episode_id ? " continuation" : ""}`}>
    <header><div><h3>{mode === "operator" ? displayModel : `Episode ${row.episode_id.slice(0, 10)}`}</h3>{mode === "operator" && <p>{row.reasoning_effort || "Unknown effort"} · {row.recorded_interface || "Unknown harness"}</p>}</div>{mode === "operator" && <label className="compare-pick"><input type="checkbox" aria-label={`Select ${row.episode_id.slice(0, 10)} for comparison`} checked={selected} onChange={onSelect} /> Compare</label>}</header>
    <p><code>{row.episode_id.slice(0, 12)}</code>{row.parent_episode_id && <> · Continuation of <code>{row.parent_episode_id.slice(0, 12)}</code></>} · {row.deck || "Unknown deck"} / {row.stake || "Unknown stake"} · <EvidenceBadge row={row} /></p>
    {mode === "operator" && <p><b>{row.outcome || row.reason || "Outcome unknown"}</b> · Attempt spend {money(row.cost_usd)} · {row.committed_actions == null ? "Unknown actions" : `${row.committed_actions} actions`}</p>}
    <small>{new Date(row.created_at).toLocaleString()}{row.fixture && " · evaluator fixture"}{!row.evaluation_eligible && " · not scored"}</small>
    {row.parent_episode_id && <small className="continuation-label">Continuation record · attempt spend is independent, not added to its parent.</small>}
    <div className="episode-actions"><button className="primary" disabled={busy} onClick={() => onExplore(row.episode_id)}>Explore decisions</button>{workbench && <button disabled={busy} onClick={() => onReview(row.episode_id)}>Review</button>}{workbench && row.branch && <button disabled={busy} onClick={() => onCompare(row.episode_id)}>Compare outcomes</button>}</div>
  </article>;
}

function EvidenceBadge({ row }: { row: Episode }) {
  return <span className={`badge ${row.evidence_kind === "NATIVE" ? "native" : "synthetic"}`}>{row.evidence_kind === "NATIVE" ? "Native" : row.fixture ? "Evaluator fixture" : "Synthetic test"}</span>;
}

function LocalComparison({ rows, displayModel, onClose }: { rows: OperatorEpisode[]; displayModel: (row: OperatorEpisode) => string; onClose: () => void }) {
  const fields: [string, keyof OperatorEpisode][] = [["Model", "model_name"], ["Reasoning effort", "reasoning_effort"], ["Harness", "recorded_interface"], ["Context policy", "context_policy"], ["Provider wire policy", "provider_wire_policy"], ["Deck", "deck"], ["Stake", "stake"], ["Outcome", "outcome"], ["Reason", "reason"], ["Attempt spend", "cost_usd"], ["Committed actions", "committed_actions"]];
  const value = (key: keyof OperatorEpisode, row: OperatorEpisode) => { if (key === "model_name") return displayModel(row); const raw = row[key]; return raw == null || raw === "" ? "Unknown" : key === "cost_usd" ? money(raw as number) : String(raw); };
  return <section className="library-comparison" aria-label="Run comparison"><div className="area-title"><h3>Descriptive comparison</h3><button onClick={onClose}>Clear comparison</button></div><p>These are separate recorded attempts; continuations may share earlier decisions. Differences describe the records and do not establish cause or effect. Spend is per attempt.</p><div className="table-wrap"><table><thead><tr><th>Field</th>{rows.map((row) => <th key={row.episode_id}>{displayModel(row)} · {row.episode_id.slice(0, 10)}</th>)}</tr></thead><tbody>{fields.map(([label, key]) => <tr key={key}><th>{label}</th>{rows.map((row) => <td key={row.episode_id}>{value(key, row)}</td>)}</tr>)}</tbody></table></div></section>;
}
