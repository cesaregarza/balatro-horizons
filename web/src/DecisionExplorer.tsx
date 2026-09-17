import { useEffect, useMemo, useRef, useState } from "react";
import { api, type DecisionLedger, type DecisionRow, type View } from "./api";
import { Board } from "./Board";
import { ReasoningSummaries } from "./ReasoningSummaries";
import {
  actionResult,
  actionTitle,
  filters,
  humanize,
  jokerChanges,
  matchesFilter,
  number,
} from "./decisionPresentation";
import "./decisions.css";
import { modelLabel } from "./modelSelection";
import { downloadDecisions, type DecisionExportFormat } from "./decisionExport";
import { modifierDescription } from "./cardPresentation";
import { ModifierLegend } from "./ModifierLegend";

export function DecisionExplorer({
  token,
  initialDecision,
  onReview,
}: {
  token: string;
  initialDecision?: number;
  onReview: (view: View) => void;
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
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [ante, setAnte] = useState("all");
  const [mobileDetail, setMobileDetail] = useState(false);
  const [boardSide, setBoardSide] = useState("before");
  const detailHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = (delay: number) => {
      if (current && liveUpdates) timer = setTimeout(load, delay);
    };
    async function load() {
      if (!current) return;
      // Hidden tabs resume on the next tick without background network traffic.
      if (document.hidden) {
        schedule(2000);
        return;
      }
      try {
        const data = await api<DecisionLedger>(
          "/review/decisions",
          "GET",
          undefined,
          token,
        );
        if (!current) return;
        setLedger(data);
        setError("");
        const rows = [
          ...data.actions,
          ...(data.uncommitted_actions || []),
        ].sort((a, b) => a.decision - b.decision);
        setSelected(
          (previous) =>
            rows.find((row) => row.decision === previous)?.decision ??
            rows.find((row) => row.decision === initialDecision)?.decision ??
            rows[0]?.decision ??
            null,
        );
        if (!data.summary?.outcome) schedule(2000);
      } catch (e) {
        if (current) {
          setError(String(e));
          schedule(5000);
        }
      } finally {
        if (current) setLoading(false);
      }
    }
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [token, refresh, initialDecision, liveUpdates]);

  const rows = useMemo(
    () =>
      [...(ledger?.actions || []), ...(ledger?.uncommitted_actions || [])].sort(
        (a, b) => a.decision - b.decision,
      ),
    [ledger],
  );
  const visible = rows.filter(
    (row) =>
      (ante === "all" || String(row.ante) === ante) &&
      matchesFilter(row, filter) &&
      [
        row.decision + 1,
        actionTitle(row),
        row.note,
        row.phase,
        row.blind,
        row.effects?.join(" "),
        row.cards?.join(" "),
        row.rejection_code,
      ]
        .join(" ")
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
  const active = visible.find((row) => row.decision === selected) || visible[0];
  const activeIndex = active ? visible.indexOf(active) : -1;
  const antes = [...new Set(rows.map((row) => row.ante))];

  useEffect(() => {
    setView(null);
    setBoardSide("before");
  }, [token, active?.decision]);

  useEffect(() => {
    if (!active) {
      setView(null);
      return;
    }
    let current = true;
    setDetailError("");
    api<View>(`/review/decisions/${active.decision}`, "GET", undefined, token)
      .then((data) => {
        if (current) setView(data);
      })
      .catch((e) => {
        if (current) setDetailError(String(e));
      });
    return () => {
      current = false;
    };
  }, [token, active?.event_id, ledger?.source_journal_head, refresh]);

  function choose(row: DecisionRow) {
    setSelected(row.decision);
    setMobileDetail(true);
    window.history.replaceState(
      null,
      "",
      `#explore/${ledger!.manifest.episode_id}/${row.decision}`,
    );
    if (window.matchMedia("(max-width: 1000px)").matches) {
      requestAnimationFrame(() => detailHeading.current?.focus());
    }
  }
  function backToChoices() {
    setMobileDetail(false);
    requestAnimationFrame(() =>
      document.getElementById(`choice-${active?.event_id}`)?.focus(),
    );
  }
  async function annotate() {
    if (!active) return;
    setBusy(true);
    setDetailError("");
    try {
      onReview(
        await api<View>(
          "/review/seek",
          "POST",
          { decision: active.decision },
          token,
        ),
      );
    } catch (e) {
      setDetailError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function exportDecisions(format: DecisionExportFormat) {
    if (!ledger) return;
    setExportError("");
    try {
      downloadDecisions(ledger, format);
    } catch {
      setExportError("Could not create the download. Try again.");
    }
  }

  return (
    <div className={`decision-explorer ${mobileDetail ? "detail-open" : ""}`}>
      <div className="review-top">
        <div>
          <p className="eyebrow">THE WHOLE RUN, AT A GLANCE</p>
          <h1>Decision explorer</h1>
        </div>
        <button disabled={loading} onClick={() => setRefresh((n) => n + 1)}>
          Refresh decisions
        </button>
      </div>
      <p className="explorer-intro">
        Browse recorded choices and outcomes, including new decisions during a
        run. Opening this view records retrospective exposure.
      </p>
      <div className="actions" aria-label="Decision exports">
        <button
          disabled={!ledger || !rows.length}
          onClick={() => exportDecisions("jsonl")}
        >
          Export JSONL
        </button>
        <button disabled={!ledger} onClick={() => exportDecisions("json")}>
          Export JSON
        </button>
      </div>
      <ModifierLegend />
      <p className="muted">
        Download all loaded decision summaries, including choices, notes and
        recorded changes. JSONL has one decision per line. Filters do not limit
        the export; a running episode is labeled as a partial snapshot.
      </p>
      {exportError && (
        <p className="error" role="alert">
          {exportError}
        </p>
      )}
      <div className="actions explorer-live-controls">
        <label className="check">
          <input
            type="checkbox"
            checked={liveUpdates}
            onChange={(e) => setLiveUpdates(e.target.checked)}
          />
          Update live
        </label>
        <span role="status" aria-live="polite" className="muted">
          {ledger?.summary?.outcome
            ? "Run finished · all recorded decisions loaded"
            : !liveUpdates
              ? "Live updates paused"
              : error
                ? "Connection interrupted · retrying"
                : "Live · updates every 2 seconds"}
        </span>
        <button
          disabled={!rows.length}
          onClick={() => {
            setSearch("");
            setFilter("all");
            setAnte("all");
            choose(rows[rows.length - 1]);
          }}
        >
          Jump to latest decision
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!ledger && loading && <p role="status">Loading decisions…</p>}
      {ledger && (
        <>
          <div className="explorer-identity">
            <strong>
              {ledger.manifest.config?.models?.[ledger.manifest.agent]
                ? modelLabel(
                    ledger.manifest.config.models[ledger.manifest.agent],
                  )
                : ledger.manifest.agent}
            </strong>
            <span
              className={`badge ${ledger.manifest.evidence_kind === "NATIVE" ? "native" : "synthetic"}`}
            >
              {ledger.manifest.evidence_kind === "NATIVE"
                ? "Native run"
                : "Synthetic test"}
            </span>
            <span>
              {ledger.manifest.config?.deck} / {ledger.manifest.config?.stake}
            </span>
            <code>{ledger.manifest.episode_id.slice(0, 10)}</code>
            {!ledger.manifest.evaluation_eligible && (
              <span className="muted">Not scored</span>
            )}
            {ledger.manifest.fixture && (
              <span className="badge">
                Evaluator fixture: {ledger.manifest.fixture}
              </span>
            )}
          </div>
          <div className="explorer-stats">
            <div>
              <small>Completed actions</small>
              <strong>{ledger.actions.length}</strong>
            </div>
            <div>
              <small>Blinds cleared</small>
              <strong>
                {ledger.rounds?.filter((round) => round.cleared).length || 0}
              </strong>
            </div>
            <div>
              <small>Blinds skipped</small>
              <strong>
                {
                  ledger.actions.filter((row) => row.type === "skip_blind")
                    .length
                }
              </strong>
            </div>
            <div>
              <small>Recorded outcome</small>
              <strong className="outcome">
                {humanize(ledger.summary?.outcome || "In progress")}
              </strong>
              {ledger.summary?.cost_usd != null && (
                <small>
                  ${ledger.summary.cost_usd.toFixed(2)} accounted cost
                </small>
              )}
            </div>
          </div>
          {ledger.summary?.reason && (
            <p className="explorer-stop">
              Run stopped: <code>{ledger.summary.reason}</code>
            </p>
          )}
          <div className="ante-navigation" aria-label="Filter by ante">
            <button
              aria-pressed={ante === "all"}
              onClick={() => {
                setAnte("all");
                setMobileDetail(false);
              }}
            >
              All antes
            </button>
            {antes.map((value) => (
              <button
                key={String(value)}
                aria-pressed={ante === String(value)}
                onClick={() => {
                  setAnte(String(value));
                  setMobileDetail(false);
                }}
              >
                {value == null ? "Unknown ante" : `Ante ${value}`}
                <span>{rows.filter((row) => row.ante === value).length}</span>
              </button>
            ))}
          </div>
          <div className="decision-toolbar">
            <label>
              Search decisions
              <input
                type="search"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setMobileDetail(false);
                }}
                placeholder="Joker, card, model note, or decision number…"
              />
            </label>
            <label>
              Action filter
              <select
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  setMobileDetail(false);
                }}
              >
                {filters.map(([value, title]) => (
                  <option key={value} value={value}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => {
                setSearch("");
                setFilter("all");
                setAnte("all");
                setMobileDetail(false);
              }}
            >
              Clear filters
            </button>
          </div>
          <p className="muted" aria-live="polite">
            {visible.length} of {rows.length} recorded requests · Includes
            uncommitted requests. Numbers start at 1.
          </p>
          {!rows.length ? (
            <div className="empty">
              <h2>No game actions recorded yet</h2>
              <p>
                {liveUpdates
                  ? "New decisions will appear here automatically."
                  : "Resume live updates or refresh to load new decisions."}
              </p>
            </div>
          ) : !visible.length ? (
            <div className="empty">
              <h2>No matching decisions</h2>
              <p>Try another term or clear the filters.</p>
            </div>
          ) : (
            <div className="decision-workspace">
              <section className="decision-list" aria-label="Recorded choices">
                {visible.map((row, index) => (
                  <div key={row.event_id}>
                    {(index === 0 || visible[index - 1].ante !== row.ante) && (
                      <h2 className="decision-group">
                        Ante {row.ante ?? "unknown"}
                      </h2>
                    )}
                    <button
                      id={`choice-${row.event_id}`}
                      className={`decision-row ${row.event_id === active?.event_id ? "selected" : ""}`}
                      aria-current={
                        row.event_id === active?.event_id ? "true" : undefined
                      }
                      onClick={() => choose(row)}
                    >
                      <span className="decision-number">
                        #{row.decision + 1}
                      </span>
                      <span className="decision-row-body">
                        <strong title={modifierDescription(row.effects)}>
                          {actionTitle(row)}
                        </strong>
                        <span
                          className={
                            !row.action_number ? "rejected" : "decision-result"
                          }
                        >
                          {actionResult(row)}
                        </span>
                        {row.note && (
                          <span className="decision-note-preview">
                            {row.note}
                          </span>
                        )}
                        {!!row.jokers_added?.length && (
                          <span className="build-change">
                            + {jokerChanges(row, "added").join(", ")}
                          </span>
                        )}
                      </span>
                      <span aria-hidden="true" className="row-arrow">
                        ↗
                      </span>
                    </button>
                  </div>
                ))}
              </section>
              {active && (
                <section
                  className="decision-detail"
                  aria-label="Decision details"
                >
                  <div className="detail-navigation">
                    <button className="back-to-choices" onClick={backToChoices}>
                      ← Back to choices
                    </button>
                    <span>
                      {activeIndex + 1} / {visible.length} matches
                    </span>
                    <button
                      aria-label="Previous matching decision"
                      disabled={activeIndex <= 0}
                      onClick={() => choose(visible[activeIndex - 1])}
                    >
                      ← Previous
                    </button>
                    <button
                      aria-label="Next matching decision"
                      disabled={activeIndex >= visible.length - 1}
                      onClick={() => choose(visible[activeIndex + 1])}
                    >
                      Next →
                    </button>
                  </div>
                  <p className="eyebrow">
                    DECISION {active.decision + 1} · ANTE {active.ante ?? "?"} ·{" "}
                    {humanize(active.phase)}
                  </p>
                  <h2
                    ref={detailHeading}
                    tabIndex={-1}
                    title={modifierDescription(active.effects)}
                  >
                    {actionTitle(active)}
                  </h2>
                  <p
                    className={
                      !active.action_number ? "rejected" : "detail-result"
                    }
                  >
                    {actionResult(active)}
                  </p>
                  {active.rejection_code && (
                    <p className="error">{active.rejection_code}</p>
                  )}
                  {!!active.cards?.length && (
                    <p className="choice-cards">{active.cards.join(" · ")}</p>
                  )}
                  {!!active.targets?.length && (
                    <p>Targets: {active.targets.join(" · ")}</p>
                  )}
                  {!!active.effects?.length && (
                    <p>{active.effects.filter(Boolean).join(" · ")}</p>
                  )}
                  {active.ordering_before && (
                    <p>Previous order: {active.ordering_before.join(" → ")}</p>
                  )}
                  {active.ordered_objects && (
                    <p>Requested order: {active.ordered_objects.join(" → ")}</p>
                  )}
                  <div className="model-note">
                    <small>Model's recorded note</small>
                    <blockquote>
                      {active.note || "No decision note recorded."}
                    </blockquote>
                    <small>This is the model's stated intention.</small>
                  </div>
                  {!!active.jokers_added?.length && (
                    <p className="build-change">
                      Added: {jokerChanges(active, "added").join(" · ")}
                    </p>
                  )}
                  {!!active.jokers_removed?.length && (
                    <p>
                      Removed: {jokerChanges(active, "removed").join(" · ")}
                    </p>
                  )}
                  {active.money_after != null && (
                    <p className="muted">
                      Cash ${number(active.money_before)} → $
                      {number(active.money_after)}
                    </p>
                  )}
                  {detailError && (
                    <p role="alert" className="error">
                      {detailError}
                    </p>
                  )}
                  {!view && !detailError && (
                    <p role="status">Loading this decision…</p>
                  )}
                  {view && (
                    <>
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={annotate}
                      >
                        Annotate this decision
                      </button>
                      <div
                        className="board-toggle"
                        aria-label="State to inspect"
                      >
                        <button
                          aria-pressed={boardSide === "before"}
                          onClick={() => setBoardSide("before")}
                        >
                          Before decision
                        </button>
                        <button
                          aria-pressed={boardSide === "after"}
                          disabled={!active.action_number || !view.transition}
                          onClick={() => setBoardSide("after")}
                        >
                          After decision
                        </button>
                      </div>
                      <Board
                        key={`${view.decision}-${boardSide}`}
                        observation={
                          boardSide === "after" && view.transition
                            ? view.transition
                            : view.observation
                        }
                      />
                      <ReasoningSummaries events={view.action_events || []} />
                      <details>
                        <summary>Exact public decision records</summary>
                        <pre>{JSON.stringify(view.action_events, null, 2)}</pre>
                      </details>
                    </>
                  )}
                </section>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
