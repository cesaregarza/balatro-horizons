import { useEffect, useState } from "react";
import { api, download, setOperatorToken, type View } from "./api";
import { Review } from "./Review";
import { DecisionExplorer } from "./DecisionExplorer";
import { Board } from "./Board";
import { Trajectory, type TimelinePoint } from "./Trajectory";
import { ModelControls } from "./ModelControls";
import { usePolling } from "./usePolling";
import {
  configureModel,
  CURRENT_HARNESS,
  effortDefault,
  harnesses,
  harnessLabel,
  modelCatalog,
  modelKey,
  modelLabel,
  type ModelConfig,
} from "./modelSelection";
type Episode = {
  episode_id: string;
  created_at: string;
  evidence_kind: string;
  deck: string;
  stake: string;
  branch: boolean;
  evaluation_eligible: boolean;
  fixture: string | null;
};
export default function App() {
  const [tab, setTab] = useState("runs"),
    [config, setConfig] = useState<any>(null),
    [episodes, setEpisodes] = useState<Episode[]>([]);
  const [agent, setAgent] = useState("heuristic"),
    [offline, setOffline] = useState(true),
    [preset, setPreset] = useState("pilot"),
    [seed, setSeed] = useState("");
  const [effort, setEffort] = useState("medium"),
    [runHarness, setRunHarness] = useState<string>(CURRENT_HARNESS);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [watch, setWatch] = useState(false),
    [status, setStatus] = useState<any>(null);
  const [review, setReview] = useState<{ token: string; view: View } | null>(
      null,
    ),
    [human, setHuman] = useState<any>(null);
  const [explorer, setExplorer] = useState<{
    token: string;
    initialDecision?: number;
  } | null>(null);
  const [panels, setPanels] = useState<any[]>([]),
    [batches, setBatches] = useState<any[]>([]),
    [report, setReport] = useState<any>(null);
  const [panel, setPanel] = useState(""),
    [batchAgents, setBatchAgents] = useState<string[]>([
      "heuristic",
      "random_legal",
    ]);
  const [priorSeedExposure, setPriorSeedExposure] = useState(false);
  const [modelSettings, setModelSettings] = useState("{}");
  const [runtimeConnection, setRuntimeConnection] = useState<{
    ready: boolean;
    code: string | null;
    message: string;
  } | null>(null);
  const [harnessInterface, setHarnessInterface] =
    useState<string>(CURRENT_HARNESS);
  const [provider, setProvider] = useState("openai"),
    [model, setModel] = useState("");
  const [inputRate, setInputRate] = useState(""),
    [outputRate, setOutputRate] = useState(""),
    [cachedRate, setCachedRate] = useState(""),
    [writeRate, setWriteRate] = useState(""),
    [priceDate, setPriceDate] = useState(new Date().toISOString().slice(0, 10));
  const [comparison, setComparison] = useState<{
    interpretation: string;
    runs: {
      episode_id: string;
      summary: Record<string, unknown> | null;
      trajectory: TimelinePoint[];
    }[];
  } | null>(null);
  const [credentials, setCredentials] = useState<Record<string, boolean>>({});
  const catalog = modelCatalog(config?.models ?? {});
  const selectedModel = catalog[agent];
  function selectAgent(value: string) {
    setAgent(value);
    if (catalog[value]) {
      setEffort(effortDefault(catalog[value]));
      setRunHarness(CURRENT_HARNESS);
    }
  }
  async function saveModelDefaults() {
    // Preserve current server limits and authorization even in a stale browser.
    const { config: latest } = await api("/bootstrap");
    const current = modelCatalog(latest.models)[agent];
    if (!current)
      throw new Error("Model is no longer configured. Refresh the workbench.");
    const chosen = configureModel(current, effort, runHarness);
    const key = modelKey(chosen);
    setConfig(
      await api("/settings", "PUT", {
        models: { ...latest.models, [key]: chosen },
        budgets: latest.budgets,
        skills: latest.skills,
      }),
    );
    return key;
  }
  async function refresh() {
    setEpisodes(await api("/episodes"));
    setPanels(await api("/panels"));
    setBatches(await api("/batches"));
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    api("/bootstrap")
      .then((data) => {
        setOperatorToken(data.operator_token);
        setConfig(data.config);
        setCredentials(data.paid_credentials);
        return refresh();
      })
      .then(() => {
        const match = window.location.hash.match(
          /^#explore\/([a-f0-9]{32})(?:\/(\d+))?$/,
        );
        if (match)
          return openExplorer(
            match[1],
            match[2] ? Number(match[2]) : undefined,
          );
      })
      .catch((e) => setError(String(e)));
  }, []);
  usePolling(
    watch,
    2000,
    () => api("/operator/status"),
    setStatus,
    (e) => setError(String(e)),
  );
  usePolling(
    Boolean(config) && tab === "runs" && !offline,
    5000,
    () => api("/operator/runtime"),
    setRuntimeConnection,
    () =>
      setRuntimeConnection({
        ready: false,
        code: "UNREACHABLE",
        message:
          "Cannot check the runtime connection. Check that the backend is available.",
      }),
  );
  usePolling(
    tab === "human",
    1000,
    () => api("/operator/human"),
    setHuman,
    (e) => setError(String(e)),
  );
  async function openReview(eid: string) {
    await run(async () => {
      const data = await api("/reviews", "POST", {
        episode_id: eid,
        prior_seed_exposure: priorSeedExposure,
      });
      setReview({ token: data.review_token, view: data.view });
      setTab("review");
      window.history.replaceState(null, "", window.location.pathname);
    });
  }
  async function openExplorer(eid: string, decision?: number) {
    await run(async () => {
      const data = await api("/reviews", "POST", {
        episode_id: eid,
        retrospective: true,
        prior_seed_exposure: priorSeedExposure,
      });
      setExplorer({ token: data.review_token, initialDecision: decision });
      setTab("explore");
      window.history.replaceState(
        null,
        "",
        `#explore/${eid}${decision == null ? "" : `/${decision}`}`,
      );
    });
  }
  if (!config)
    return (
      <main className="loading">
        <h1>Balatro Horizons</h1>
        <p>{error || "Opening the workbench…"}</p>
      </main>
    );
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="mark">♠</span>
          <div>
            <strong>BALATRO</strong>
            <span>HORIZONS</span>
          </div>
        </div>
        <p className="sidebar-label">YOUR WORKBENCH</p>
        <nav>
          {[
            ["runs", "Runs"],
            ["explore", "Decision explorer"],
            ["review", "Horizon review"],
            ["human", "Human control"],
            ["batches", "Batches & reports"],
            ["models", "Models & budgets"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => {
                setTab(id);
                if (id !== "explore")
                  window.history.replaceState(
                    null,
                    "",
                    window.location.pathname,
                  );
              }}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="dot" /> LOCAL WORKSPACE
          <p>
            Native decisions.
            <br />
            Inspectable continuations.
          </p>
          <small>Red Deck · Gold Stake</small>
        </div>
      </aside>
      <main>
        {tab !== "explore" && (
          <header>
            <span>
              {tab === "review" ? "EXPERT ANALYSIS" : "RUN WORKBENCH"}
            </span>
            <div className="header-right">
              <span className="badge">1 worker</span>
              <button onClick={() => run(refresh)}>Refresh</button>
            </div>
          </header>
        )}
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="notice">
            {notice}
          </div>
        )}
        {tab === "runs" && (
          <>
            <div className="hero">
              <p className="eyebrow">FULL RUNS. LONGER HORIZONS.</p>
              <h1>
                Every choice leaves
                <br />a future to inspect.
              </h1>
              <p>
                Run an agent through Balatro, then examine the tradeoffs
                <br className="desktop" /> between scoring now, surviving next,
                and building ahead.
              </p>
            </div>
            <div className="split">
              <section className="panel run-setup">
                <h2>Start a run</h2>
                <div className="form-row">
                  <label>
                    Model
                    <select
                      aria-label="Model"
                      value={agent}
                      onChange={(e) => selectAgent(e.target.value)}
                      disabled={busy}
                    >
                      <optgroup label="Models">
                        {Object.entries(catalog).map(([key, value]) => (
                          <option key={key} value={key}>
                            {modelLabel(value)}
                          </option>
                        ))}
                      </optgroup>
                      <optgroup label="Baselines & human control">
                        <option value="heuristic">Heuristic baseline</option>
                        <option value="random_legal">
                          Random legal baseline
                        </option>
                        <option value="human">Human player</option>
                      </optgroup>
                    </select>
                  </label>
                  <label>
                    Game configuration
                    <select
                      value={preset}
                      onChange={(e) => setPreset(e.target.value)}
                    >
                      <option value="pilot">
                        Red / Gold · strategic pilot
                      </option>
                      <option value="smoke">Red / White · smoke</option>
                    </select>
                  </label>
                </div>
                {selectedModel && (
                  <>
                    <ModelControls
                      model={selectedModel}
                      effort={effort}
                      harness={runHarness}
                      onEffort={setEffort}
                      onHarness={setRunHarness}
                      disabled={busy}
                    />
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await saveModelDefaults();
                          setNotice("Model defaults saved. No run started.");
                        })
                      }
                    >
                      Save model defaults
                    </button>
                    <p className="muted">
                      Starting a run also saves these defaults. Each run keeps
                      its exact settings.
                    </p>
                  </>
                )}
                <label>
                  Private seed <span className="muted">optional</span>
                  <input
                    value={seed}
                    onChange={(e) => setSeed(e.target.value)}
                    placeholder="Generate an unseen seed"
                    autoComplete="off"
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={offline}
                    onChange={(e) => {
                      setOffline(e.target.checked);
                      setRuntimeConnection(null);
                    }}
                  />
                  Synthetic pipeline test
                </label>
                <p className="muted">
                  {offline
                    ? "Synthetic episodes test the application and are labeled throughout."
                    : "Native autonomous runs require passing environment and action-coverage gates."}
                </p>
                <div className="actions">
                  <button
                    className="primary"
                    disabled={
                      busy || (!offline && runtimeConnection?.ready !== true)
                    }
                    onClick={() =>
                      run(async () => {
                        const chosenAgent = selectedModel
                          ? await saveModelDefaults()
                          : agent;
                        const r = await api("/runs", "POST", {
                          agent: chosenAgent,
                          offline,
                          preset,
                          seed: seed || null,
                        });
                        setNotice("Run created: " + r.episode_id.slice(0, 10));
                        await refresh();
                        if (agent === "human") setTab("human");
                      })
                    }
                  >
                    Start {offline ? "test episode" : "native run"}{" "}
                    <span>↗</span>
                  </button>
                  <button
                    onClick={() =>
                      run(async () => {
                        await api("/stop", "POST", {});
                        setNotice(
                          "Stop requested. Any in-flight action will be recorded.",
                        );
                      })
                    }
                  >
                    Stop worker
                  </button>
                </div>
                {!offline && runtimeConnection && (
                  <p
                    role="status"
                    className={runtimeConnection.ready ? "muted" : "error"}
                  >
                    {runtimeConnection.message}
                  </p>
                )}
              </section>
              <section className="panel live">
                <p className="eyebrow">OPERATOR VIEW</p>
                <h2>Live progress</h2>
                <p>
                  Watching reveals the agent and its progress. That exposure is
                  recorded before later review.
                </p>
                <button onClick={() => setWatch(!watch)}>
                  {watch ? "Hide live status" : "Watch live status"}
                </button>
                {watch && status && (
                  <div>
                    <p className="status-line">
                      <span
                        className={"dot " + (status.running ? "" : "idle")}
                      />
                      {status.running ? "Worker running" : "Worker idle"}
                    </p>
                    {status.error && <p className="error">{status.error}</p>}
                    {status.episodes.slice(0, 3).map((r: any) => (
                      <p key={r.episode_id}>
                        <b>
                          {config.models[r.agent]
                            ? modelLabel(config.models[r.agent])
                            : r.agent}
                        </b>{" "}
                        ·{" "}
                        {r.summary?.outcome ||
                          r.progress?.phase ||
                          "In progress"}
                        <br />
                        <small>
                          {r.summary?.committed_actions ??
                            r.progress?.committed_actions ??
                            0}{" "}
                          actions · $
                          {(
                            r.summary?.cost_usd ??
                            r.progress?.cost_usd ??
                            0
                          ).toFixed(4)}
                        </small>
                      </p>
                    ))}
                  </div>
                )}
              </section>
            </div>
            <section className="panel">
              <div className="area-title">
                <h2>Run library</h2>
                <span>{episodes.length} recorded episodes</span>
              </div>
              <p className="muted">
                Explore decisions to see the whole run, including its outcome.
                Review → opens staged prospective review. Model names and
                outcomes stay hidden in this list.
              </p>
              <label className="check">
                <input
                  type="checkbox"
                  checked={priorSeedExposure}
                  onChange={(e) => setPriorSeedExposure(e.target.checked)}
                />
                I have previously played or watched the seed of the run I am
                about to review.
              </label>
              {episodes.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Episode</th>
                        <th>Configuration</th>
                        <th>Evidence</th>
                        <th>Created</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {episodes.map((e) => (
                        <tr key={e.episode_id}>
                          <td>
                            <code>{e.episode_id.slice(0, 10)}</code>
                            {e.branch && <span className="badge">branch</span>}
                          </td>
                          <td>
                            {e.deck} / {e.stake}
                          </td>
                          <td>
                            <span
                              className={
                                "badge " +
                                (e.evidence_kind === "NATIVE"
                                  ? "native"
                                  : "synthetic")
                              }
                            >
                              {e.evidence_kind === "NATIVE"
                                ? "Native"
                                : "Synthetic test"}
                            </span>
                          </td>
                          <td>
                            {new Date(e.created_at).toLocaleString()}
                            {e.fixture && <small> · evaluator fixture</small>}
                            {!e.evaluation_eligible && (
                              <small> · not scored</small>
                            )}
                          </td>
                          <td>
                            <button
                              className="primary"
                              disabled={busy}
                              onClick={() => openExplorer(e.episode_id)}
                            >
                              Explore decisions
                            </button>
                            <button onClick={() => openReview(e.episode_id)}>
                              Review →
                            </button>
                            {e.branch && (
                              <button
                                onClick={() =>
                                  run(async () => {
                                    setComparison(
                                      await api(
                                        "/operator/branches/" +
                                          e.episode_id +
                                          "/comparison",
                                      ),
                                    );
                                    setTab("comparison");
                                  })
                                }
                              >
                                Compare outcomes
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty">
                  <span>♧</span>
                  <h3>Your first run starts here.</h3>
                  <p>
                    Record an episode to inspect its decisions and future
                    branches.
                  </p>
                </div>
              )}
            </section>
          </>
        )}
        {tab === "explore" &&
          (explorer ? (
            <DecisionExplorer
              key={explorer.token}
              token={explorer.token}
              initialDecision={explorer.initialDecision}
              onReview={(view) => {
                setReview({ token: explorer.token, view });
                setTab("review");
              }}
            />
          ) : (
            <section className="empty">
              <h1>Explore a run</h1>
              <p>
                Choose Explore decisions in the run library for the full
                timeline, choices, and results.
              </p>
              <button onClick={() => setTab("runs")}>Open run library</button>
            </section>
          ))}
        {tab === "review" &&
          (review ? (
            <Review
              key={`${review.token}:${review.view.decision}`}
              token={review.token}
              initial={review.view}
              onExplore={(decision) =>
                openExplorer(review.view.episode_id, decision)
              }
              onBranch={(eid) => {
                setNotice("Branch created: " + eid.slice(0, 10));
                refresh();
                setTab("human");
              }}
            />
          ) : (
            <section className="empty">
              <h1>Review a run</h1>
              <p>
                Choose an episode from the run library to begin with only the
                information available at its first decision.
              </p>
              <button onClick={() => setTab("runs")}>Open run library</button>
            </section>
          ))}
        {tab === "comparison" && comparison && (
          <>
            <h1>Alternative continuation</h1>
            <p>{comparison.interpretation}</p>
            <p className="notice">
              Outcome exposure is recorded for both runs.
            </p>
            {comparison.runs.map((r, i) => (
              <section key={r.episode_id}>
                <h2>{i ? "Branch" : "Original run"}</h2>
                <pre>{JSON.stringify(r.summary, null, 2)}</pre>
                <Trajectory points={r.trajectory} />
              </section>
            ))}
          </>
        )}
        {tab === "human" && (
          <>
            <h1>Human control</h1>
            <p className="muted">
              Your actions use the same public information and validation as
              model actions.
            </p>
            {human?.waiting ? (
              <Board
                key={human.context.observation.observation_id}
                observation={human.context.observation}
                onAction={(action) =>
                  run(async () => {
                    await api("/operator/human", "POST", {
                      kind: "action",
                      envelope: {
                        observation_id:
                          human.context.observation.observation_id,
                        action,
                      },
                    });
                    setHuman(null);
                  })
                }
              />
            ) : (
              <section className="empty">
                <span>♧</span>
                <h3>No human decision is waiting.</h3>
                <p>Start a human run or take over a certified branch.</p>
              </section>
            )}
          </>
        )}
        {tab === "models" && (
          <>
            <h1>Models & budgets</h1>
            <p>
              Direct provider access with a shared gameplay context. Keys are
              read from the backend environment.
            </p>
            <section className="panel">
              <h2>Game knowledge</h2>
              <label>
                Skill access
                <select
                  aria-label="Skill access"
                  value={config.skills ?? "balatro-guide-v1"}
                  onChange={(e) =>
                    setConfig({ ...config, skills: e.target.value })
                  }
                >
                  <option value="balatro-guide-v1">
                    Balatro guide — rules and strategy
                  </option>
                  <option value="none">Native rules only</option>
                </select>
              </label>
              <p className="muted">
                The model sees a small skill catalog and reads chapters when
                needed. Each run keeps its own frozen copy.
              </p>
              <button
                onClick={() =>
                  run(async () => {
                    setConfig(
                      await api("/settings", "PUT", {
                        models: config.models,
                        budgets: config.budgets,
                        skills: config.skills ?? "balatro-guide-v1",
                      }),
                    );
                    setNotice("Skill access saved for new runs.");
                  })
                }
              >
                Save skill access
              </button>
            </section>
            <div className="split">
              <section className="panel">
                <h2>Model connection & pricing</h2>
                <div className="form-row">
                  <label>
                    Provider
                    <select
                      value={provider}
                      onChange={(e) => setProvider(e.target.value)}
                    >
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic</option>
                    </select>
                  </label>
                </div>
                <label>
                  Exact model identifier
                  <input
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="Provider model ID"
                  />
                </label>
                <div className="form-row">
                  <label>
                    Input $ / million tokens
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={inputRate}
                      onChange={(e) => setInputRate(e.target.value)}
                    />
                  </label>
                  <label>
                    Output $ / million tokens
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={outputRate}
                      onChange={(e) => setOutputRate(e.target.value)}
                    />
                  </label>
                </div>
                {provider === "openai" && (
                  <div className="field-grid">
                    <label>
                      Cache read $ / million tokens
                      <input
                        type="number"
                        min="0"
                        step="any"
                        value={cachedRate}
                        onChange={(e) => setCachedRate(e.target.value)}
                      />
                    </label>
                    <label>
                      Cache write $ / million tokens
                      <input
                        type="number"
                        min="0"
                        step="any"
                        value={writeRate}
                        onChange={(e) => setWriteRate(e.target.value)}
                      />
                    </label>
                    <p className="muted">
                      Requires GPT-5.6 or later. Enter the standard input rate
                      above and the separate read/write rates here. Spending
                      reservations use the highest input rate.
                    </p>
                  </div>
                )}
                <label>
                  Price verified on
                  <input
                    type="date"
                    value={priceDate}
                    onChange={(e) => setPriceDate(e.target.value)}
                  />
                </label>
                <p className="muted">
                  {credentials[provider]
                    ? "Credential is available."
                    : "Credential has not been configured."}
                </p>
                <label>
                  Harness
                  <select
                    aria-label="Harness"
                    value={harnessInterface}
                    onChange={(e) => setHarnessInterface(e.target.value)}
                  >
                    {harnesses.map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="muted">
                  Includes on-demand tools, provider continuation within each
                  decision, and prompt caching for supported OpenAI models.
                  Saving updates defaults for new runs; existing runs keep their
                  recorded harness.
                </p>
                <label>
                  Additional model settings (JSON)
                  <textarea
                    value={modelSettings}
                    onChange={(e) => setModelSettings(e.target.value)}
                    placeholder={
                      '{"reasoning_effort":"medium","reasoning_summary":"auto"}'
                    }
                  />
                </label>
                <button
                  disabled={
                    busy ||
                    !model ||
                    inputRate === "" ||
                    outputRate === "" ||
                    (provider === "openai" &&
                      (cachedRate === "" || writeRate === ""))
                  }
                  onClick={() =>
                    run(async () => {
                      const { config: latest } = await api("/bootstrap");
                      const key = modelKey({
                        provider: provider as ModelConfig["provider"],
                        model,
                      });
                      const models = {
                        ...latest.models,
                        [key]: {
                          provider,
                          model,
                          input_usd_per_million: +inputRate,
                          output_usd_per_million: +outputRate,
                          ...(provider === "openai"
                            ? {
                                cached_input_usd_per_million: +cachedRate,
                                cache_write_input_usd_per_million: +writeRate,
                              }
                            : {}),
                          pricing_date: priceDate,
                          settings: {
                            ...(latest.models[key]?.settings ?? {}),
                            ...JSON.parse(modelSettings),
                            harness_interface: harnessInterface,
                          },
                        },
                      };
                      setConfig(
                        await api("/settings", "PUT", {
                          models,
                          budgets: latest.budgets,
                          skills: latest.skills,
                        }),
                      );
                      setNotice("Model configuration saved.");
                    })
                  }
                >
                  Save model
                </button>
                <h3>Configured models</h3>
                <p className="muted">
                  Choose reasoning effort and save model defaults in Start a
                  run.
                </p>
                {Object.entries(catalog).map(([name, value]) => (
                  <p key={name}>
                    <b>{modelLabel(value)}</b>
                    {" · "}
                    {harnessLabel(value)}{" "}
                    <button
                      onClick={() => {
                        setProvider(value.provider);
                        setModel(value.model);
                        setInputRate(String(value.input_usd_per_million));
                        setOutputRate(String(value.output_usd_per_million));
                        setCachedRate(
                          value.cached_input_usd_per_million == null
                            ? ""
                            : String(value.cached_input_usd_per_million),
                        );
                        setWriteRate(
                          value.cache_write_input_usd_per_million == null
                            ? ""
                            : String(value.cache_write_input_usd_per_million),
                        );
                        setPriceDate(value.pricing_date);
                        setHarnessInterface(CURRENT_HARNESS);
                        setModelSettings(
                          JSON.stringify({
                            ...value.settings,
                            harness_interface: CURRENT_HARNESS,
                          }),
                        );
                      }}
                    >
                      Edit connection
                    </button>
                  </p>
                ))}
              </section>
              <section className="panel">
                <h2>Spending controls</h2>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={config.budgets.paid_calls_enabled}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        budgets: {
                          ...config.budgets,
                          paid_calls_enabled: e.target.checked,
                        },
                      })
                    }
                  />
                  Enable paid execution
                </label>
                {[
                  ["max_episode_cost_usd", "Episode ceiling ($)"],
                  ["max_batch_cost_usd", "Batch ceiling ($)"],
                ].map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      type="number"
                      min="0.01"
                      step="any"
                      value={config.budgets[key] ?? ""}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          budgets: {
                            ...config.budgets,
                            [key]: e.target.value ? +e.target.value : null,
                          },
                        })
                      }
                    />
                  </label>
                ))}
                <p className="muted">
                  Every request reserves its maximum configured cost before
                  being sent. Unknown usage retains the reservation.
                </p>
                <button
                  onClick={() =>
                    run(async () => {
                      setConfig(
                        await api("/settings", "PUT", {
                          models: config.models,
                          budgets: config.budgets,
                        }),
                      );
                      setNotice("Budget settings saved.");
                    })
                  }
                >
                  Save spending limits
                </button>
                <dl>
                  <dt>Committed actions</dt>
                  <dd>{config.budgets.max_game_actions}</dd>
                  <dt>Provider calls</dt>
                  <dd>{config.budgets.max_provider_calls}</dd>
                  <dt>Helpers per decision</dt>
                  <dd>{config.budgets.max_helper_calls_per_decision}</dd>
                </dl>
              </section>
            </div>
          </>
        )}
        {tab === "batches" && (
          <>
            <h1>Batches & reports</h1>
            <p>
              Freeze a shared seed panel and configuration before comparing
              agents.
            </p>
            <section className="panel">
              <h2>Plan an exploratory batch</h2>
              <button
                onClick={() =>
                  run(async () => {
                    const p = await api("/panels", "POST", { count: 20 });
                    setPanel(p.panel_id);
                    await refresh();
                  })
                }
              >
                Generate 20 private development seeds
              </button>
              <div className="form-row">
                <label>
                  Seed panel
                  <select
                    value={panel}
                    onChange={(e) => setPanel(e.target.value)}
                  >
                    <option value="">Choose a panel</option>
                    {panels.map((p) => (
                      <option key={p.panel_id} value={p.panel_id}>
                        {p.panel_id.slice(0, 10)} · {p.count} seeds
                      </option>
                    ))}
                  </select>
                </label>
                <fieldset>
                  <legend>Models & players</legend>
                  {[
                    ["heuristic", "Heuristic baseline"],
                    ["random_legal", "Random legal baseline"],
                    ...Object.entries(catalog).map(([key, value]) => [
                      key,
                      modelLabel(value),
                    ]),
                  ].map(([key, label]) => (
                    <label key={key} className="check">
                      <input
                        type="checkbox"
                        checked={batchAgents.includes(key)}
                        onChange={(e) =>
                          setBatchAgents(
                            e.target.checked
                              ? [...batchAgents, key]
                              : batchAgents.filter((a) => a !== key),
                          )
                        }
                      />
                      {label}
                    </label>
                  ))}
                  <p className="muted">
                    New plans use the current harness and each model’s saved
                    effort. OpenAI models need cache read/write prices in
                    Settings. Existing frozen plans keep their original
                    settings.
                  </p>
                </fieldset>
              </div>
              <button
                disabled={!panel || !batchAgents.length || busy}
                onClick={() =>
                  run(async () => {
                    const { config: latest } = await api("/bootstrap");
                    const available = modelCatalog(latest.models);
                    const chosen = { ...latest.models };
                    for (const key of batchAgents) {
                      if (["heuristic", "random_legal"].includes(key)) continue;
                      if (!available[key])
                        throw new Error(
                          "Model is no longer configured. Refresh the workbench.",
                        );
                      chosen[key] = configureModel(
                        available[key],
                        effortDefault(available[key]),
                        CURRENT_HARNESS,
                      );
                    }
                    setConfig(
                      await api("/settings", "PUT", {
                        models: chosen,
                        budgets: latest.budgets,
                        skills: latest.skills,
                      }),
                    );
                    await api("/batches", "POST", {
                      panel_id: panel,
                      agents: batchAgents,
                      replicates: 2,
                    });
                    await refresh();
                  })
                }
              >
                Freeze plan · 2 replicates
              </button>
            </section>
            {batches.map((b) => (
              <section key={b.batch_id} className="panel">
                <div className="area-title">
                  <h3>Batch {b.batch_id.slice(0, 10)}</h3>
                  <span>{b.slots.length} planned slots</span>
                </div>
                <p>
                  {b.config.deck} / {b.config.stake} · {b.split}
                </p>
                <div className="actions">
                  <button
                    onClick={() =>
                      run(async () => {
                        await api("/batches/" + b.batch_id + "/run", "POST", {
                          offline,
                        });
                        setNotice("Batch queued.");
                      })
                    }
                  >
                    Run {offline ? "synthetic test" : "native batch"}
                  </button>
                  <button
                    onClick={() =>
                      run(async () =>
                        setReport(
                          await api("/batches/" + b.batch_id + "/report"),
                        ),
                      )
                    }
                  >
                    Reveal report
                  </button>
                  <button
                    onClick={() =>
                      run(async () => {
                        const exported = await api(
                          "/batches/" + b.batch_id + "/export",
                          "POST",
                          {},
                        );
                        await download(
                          exported.download,
                          "balatro-horizons-" + b.batch_id + ".json",
                        );
                        setNotice(
                          "Public export passed privacy checks and was downloaded.",
                        );
                      })
                    }
                  >
                    Export public results
                  </button>
                </div>
              </section>
            ))}
            {report && (
              <section className="panel">
                <h2>
                  {report.evidence_kinds?.includes("SYNTHETIC_TEST")
                    ? "Synthetic test outcomes"
                    : "Autonomous outcomes"}
                </h2>
                {report.evidence_kinds?.includes("SYNTHETIC_TEST") && (
                  <p className="notice">
                    Application accounting test only. These are not native
                    benchmark results.
                  </p>
                )}
                <p>
                  Valid outcomes include protocol failure, agent abort, and
                  budget exhaustion. Coverage includes unresolved slots.
                </p>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Agent</th>
                        <th>Wins / valid</th>
                        <th>Coverage</th>
                        <th>Missing-outcome bounds</th>
                        <th>All-attempt cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(report.agents).map(([a, r]: any) => (
                        <tr key={a}>
                          <td>{a}</td>
                          <td>
                            {r.wins} / {r.valid}
                          </td>
                          <td>
                            {r.valid} / {r.planned}
                          </td>
                          <td>
                            {r.missing_outcome_bounds
                              ?.map((x: number) => (x * 100).toFixed(1) + "%")
                              .join(" – ")}
                          </td>
                          <td>${r.all_attempt_cost_usd.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <details>
                  <summary>Full accounting and paired comparisons</summary>
                  <pre>{JSON.stringify(report, null, 2)}</pre>
                </details>
              </section>
            )}
          </>
        )}
        <footer>
          Balatro Horizons · Original runs remain immutable. Assisted branches
          are separate diagnostic evidence.
        </footer>
      </main>
    </div>
  );
}
