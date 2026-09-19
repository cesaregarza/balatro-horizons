# Balatro Horizons

A local benchmark workbench for studying how agents balance survival in the current
blind, preparation for the current ante, and development across a full Balatro run.
Models play complete native games; researchers can review decisions prospectively,
annotate tradeoffs, and test alternative continuations at certified decisions.
Red Deck / Gold Stake is the evaluation default; Red / White is the plumbing configuration.

## What this benchmark studies

The central question is **when an agent applies relevant knowledge and how it
trades present resources against future needs**. For example, a revealed boss may
require changing a successful build, while an already strong build may create an
opportunity to preserve income instead of buying more immediate scoring power.
The research horizon definitions are immediate = this blind, short = this ante,
and long = the full run. Existing annotation labels are preserved until a
versioned rubric migration is selected.

The workbench brings these capabilities together:

- **Review before reveal:** inspect the information available at a decision and
  annotate before revealing the model's action and consequences. The server
  tracks prior exposure, including watching a run live.
- **Expert diagnosis:** annotate decisions or intervals, alternatives, confidence,
  and supporting events with revision history. The research direction distinguishes
  recognition, valuation, and planning/adaptation; a complete taxonomy-specific
  annotation workflow remains a proposal.
- **Testable alternatives:** create an immutable child run for an override,
  continuation, or human takeover only where replay/restoration is certified.
  A successful alternative supports that continuation; it does not establish a
  uniquely optimal move or assign a causal share of the original failure.
- **Inspectable information and protocol:** public observations mask concealed
  information; prompt, skills, provider settings, memory policy, and delivered
  contexts are recorded. The frozen protocol makes changes in assistance visible.
- **Outcome and coverage accounting:** autonomous full-run wins are the primary
  performance outcome. Reports retain unresolved slots, infrastructure failures,
  funding interruptions, and all-attempt costs. Assisted branches are excluded
  from autonomous scores.

The intended safety application is an early-warning evaluation of a proposed
reasoning prerequisite for longer-term strategic behavior. **The relationship to
scheming is a research hypothesis, not a validated interpretation of a Balatro
score.** Automatic horizon scores, a public leaderboard, and a stake staircase
are not implemented. See the [research reconciliation](docs/research/reconciliation-2026-09-14.md)
for accepted direction and still-unselected measurement proposals.

### Relationship to other game benchmarks

Long-horizon game evaluation and native Balatro play already have substantial
precedents. The distinction here is the focus and the expert-review/branching
workflow described above, not a claim to be the first long-horizon game benchmark.

| Project | Documented focus | Balatro Horizons emphasis |
| --- | --- | --- |
| [BALROG](https://arxiv.org/abs/2411.13543) | Evaluates LLM/VLM agents across multiple game environments, explicitly including long-horizon planning and exploration. | Examine blind/ante/run tradeoffs within one native game, using prospective expert review and tested alternative continuations. |
| [Evalatro](https://github.com/alesha-pro/evalatro) | Native Balatro runs, full decision replays, and a public leaderboard with a composite 0–100 score. | Autonomous wins plus coverage and failure accounting, with expert annotations and certified branches as diagnostic evidence. |
| [BalatroBench](https://github.com/coder/balatrobench) | Analyzes BalatroLLM run artifacts and generates interactive model/strategy leaderboards. | Collect and inspect the decision evidence, control prospective exposure, and intervene through separate immutable continuations. |

These are comparisons of documented project emphasis, checked 2026-09-16, not an
exhaustive novelty review or a claim that other projects cannot support similar
research. Native execution, long trajectories, and replay viewers are shared ideas.

## Implementation status

The native adapter runs the licensed Windows game in `D:\BalatroHorizonsRuntime`.
Python, the browser application, journals, and private checkpoints live on Linux.
Synthetic episodes are explicitly labeled application tests. The
[September 16 release](docs/release-2026-09-16.md) records local native action,
replay, branch, and OpenAI Terra smoke evidence for its exact source/runtime
fingerprints. Anthropic live validation and headless equivalence remain pending.
Executable changes, including the defaults refactor, require matching native
certification before deployment; the earlier release is not evidence for a new
fingerprint.

## Source checkout and private local files

This repository contains application source, tests, dependency locks, the canonical
Balatro guide, and setup documentation. Credentials, run journals, seeds, saves,
operator settings, downloaded mod sources, worktrees, and generated verification
reports stay on the local machine and are excluded from Git.

Native verification statements in these documents describe the original local
installation. A fresh checkout must install its own licensed runtime and pass its
own gates; it does not inherit native certificates or paid execution authorization.
The repository does not include Balatro executables, game assets, or personal saves.

## Open the workbench

```bash
git clone https://github.com/cesaregarza/balatro-horizons.git
cd balatro-horizons
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run bh review --port 8765
```

Open **http://127.0.0.1:8765**. Use Python 3.12 and the tested Node 22 LTS version in `.nvmrc` (22.12.0). The checked-in `uv.lock` and `web/package-lock.json` pin dependencies. The browser server binds only to loopback.

Start with **Synthetic pipeline test** to inspect the complete application without a game or credentials. For native runs, clear that option after the runtime gates pass. The run library conceals agent identity and outcomes. Watching live status, opening a comparison, or revealing an outcome records your exposure before subsequent annotations.

Review advances through available information, agent action, and consequences. You can annotate individual decisions or intervals and preserve revisions. The revealed trajectory shows resources and build changes only through the review cursor. A certified decision can create an agent continuation, one-action override, three-action human sequence, or human takeover. Human controls preserve explicit card selection and order; moving cards left can produce any permutation. Comparisons reveal both outcomes and exclude all assisted branches from autonomous scores.

## Native setup and checks

```bash
uv run python scripts/bootstrap_native.py
uv run python scripts/audit_runtime.py
uv run bh doctor --json
```

The installer reads the licensed installation at `D:\SteamLibrary\steamapps\common\Balatro` and only writes the dedicated runtime. Keep the game closed during instrumentation refreshes. The runtime pins LÖVE's save identity to `BalatroHorizons`, prevents personal Steam integration, and initializes each process from native default profile data with all content unlocked. It checks the loaded instrumentation manifest against disk. A copied executable alone is insufficient to pass isolation.

The backend uses the fixed `native/bridge.ps1` script through Windows PowerShell. Ordinary playing policies receive none of its evaluator/debug methods. Visible execution at native speed is the supported mode. Rendering suppression and acceleration remain disabled pending their own equivalence tests.

```bash
# Ordinary-mechanics native baseline, recorded as calibration rather than benchmark evidence
uv run bh run --config configs/smoke.yaml --agent heuristic --calibration
# A synthetic episode
uv run bh run --offline --agent heuristic
# Check one recorded decision; the episode's private configuration is reused
uv run bh replay verify --episode-id EPISODE_ID --decision 0
uv run bh replay verify --episode-id EPISODE_ID --decision 0 --mode seed_prefix
```

Certificates require three fresh-process repetitions and compare public states, private continuation fingerprints, ordering, and terminal results. The evidence is tied to execution/restoration source and the native environment. API, report, and presentation changes have separate tests and do not invalidate native replay certificates. Failed rechecks disable that restoration path and preserve a private first-divergence artifact. Native saves do not establish fidelity merely by succeeding. Seed-prefix restoration re-executes recorded actions, checks every intermediate state, and is enabled only by a passing certificate. Only previously certified decisions can branch.

## Explore decisions

For an at-a-glance run review, choose **Runs → Explore decisions**. Browse by ante,
filter or search choices, inspect before/after state, and jump into annotation.
The explorer explicitly reveals the whole run; **Review →** retains prospective
staged reveals. See [decision exploration and summaries](docs/decision-summaries.md).

## Providers and spending

The [skill-enabled harness](docs/harness-skills.md) gives new runs a compact catalog of twelve Balatro skills and on-demand chapter reads. The default is the canonical guide; **Models & budgets → Game knowledge** offers a native-rules-only comparison. Knowledge is frozen per run and inherited by branches.

The [OpenAI/Luna harness guide](docs/openai-harness.md) includes the pinned smoke preset and a one-run command with a persistent campaign spending ledger.

The [named-tools revision](docs/harness-tools-v2.md) documents the earlier interface.
The source selector uses **Current harness (v7)**: direct gameplay tools,
on-demand inspection, provider continuation, an editable run notebook, and bounded
recent working context across actions. Models receive notebook-maintenance
guidance and can attach a note edit to an action without another provider call.
[Working context and notebook maintenance](docs/working-memory-v1.md) describes
the limits, validation and branch boundaries; [v6 notebook documentation](docs/run-notebook-v1.md)
records the preceding interface. Historical configurations retain their recorded
interfaces. Each native installation needs certification matching its source;
the previously verified v6 release does not certify v7.

The [Balatro Horizons Guide](docs/balatro-guide/README.md) is the canonical rules
and strategy reference for the harness. Its modular chapters compile into
`docs/balatro-guide/rules.json` for frozen topic lookup. Rebuild and validate it with:

```bash
uv run python scripts/package_balatro_guide.py \
  --guide docs/balatro-guide --output docs/balatro-guide.zip \
  --report reports/verification/balatro-guide.json
```

Use **Models & budgets** to enter explicit model identifiers, input/output prices and pricing date, provider settings, and both episode and batch spending ceilings. Put `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` in the backend process environment; credentials are never browser settings. Restart the backend after setting credentials. Enable paid calls explicitly for the configured run. The exploratory 20-seed × 2-replicate × 2-model preset does not authorize spending.

The OpenAI Responses and Anthropic Messages adapters share the same public boundary and tool definitions within each interface version. The original interface uses `operate`; the new interface exposes named gameplay and inspection tools. Each response may submit one game action (optionally carrying a note edit in v7) or permitted helper call. Context includes the current observation and up to 4,096 characters of agent-authored memory. V7 also retains up to three completed decisions and their recent helper receipts, within explicit byte bounds. The original interface supplies up to 20 recent public events automatically; named tools supply two and make the full 20 available on demand. No built-in provider search or computer tools are enabled.

Reservations use configured token ceilings and prices before every request, including retries. Unknown usage retains the reservation. Usage-based cost estimates use the configured prices; an overage is recorded even if it exceeds the reservation, and subsequent calls stop at the cap. Luna's live smoke exercised reservation and usage settlement; these estimates are not provider invoices. Paid execution requires operator-supplied model settings and spending authorization. No paid smoke run is implicit in installation or tests.

## Storage and exports

- `data/public_runs/`: immutable manifests and hash-chained append-only event journals.
- `data/private_runs/`: seeds, raw engine observations, native saves, identity mappings, budgets, and certificate evidence. Keep private.
- `data/review/`: exposure records, review cursors, and append-only annotation revisions. Local owner only.
- `data/index.sqlite3`: rebuildable episode index, never the source of truth.
- `private/`: local environment lock, frozen licensed rules, and capability selection. Keep private.
- `reports/verification/`: local verification artifacts. Some reference private episode evidence; do not publish the directory wholesale.

Use the application's **Export public bundle** or `bh export --public` to generate schema-selected, privacy-scanned exports. Exports include annotation revisions without reviewer identity by default. They do not copy private directories. `bh human` reads the current human decision from the shared server on port 8765, and `bh human --action-file operation.json` submits an operation. CLI human runs and takeovers use that same server. Stop the worker before `bh recover`; recovery retains torn tails privately and marks unresolved actions as infrastructure failures.

Batch reports include planned slots, first valid outcomes, wins, coverage, unresolved slots, outcome categories, missing-outcome bounds, and costs across all attempts. Confidence intervals resample seeds with their replicates. Paired comparisons retain seed/replicate matching. Human interventions are diagnostic evidence, not autonomous wins or proofs of optimal play.

Campaign funding is separate from the agent's episode budget. An unfunded next
paid slot creates no episode; a campaign-only interruption leaves the current
slot unresolved and retains its costs. An episode-only cost refusal remains a
valid `BUDGET_EXHAUSTED` non-win. If both caps bind, the current attempt is valid
and the campaign also stops. Funding stops are durable: rerunning the same frozen
batch cannot resume it, even with a cheaper or scripted agent next. Reports and
public exports include `scheduling_stop`; old generic cost stops retain their
original meaning. See [exact semantics and validation](docs/campaign-budget-fix.md).

The `tools_v5` harness preserves provider-native reasoning and tool-call blocks
across helper calls within one decision, then deliberately resets provider state
after the game action. It gives OpenAI and Anthropic the same fixed tool catalog
and locally enforces phase availability. New traces distinguish missing,
multiple, unavailable, malformed, incomplete, and refused operations. Public
exports omit opaque continuation material. Existing interface versions retain
their behavior. See [protocol details and limits](docs/provider-continuation-v1.md).

## Validation

The [September 16 release handoff](docs/release-2026-09-16.md) records the deployed
changes, native certificates, provider smoke, and remaining verification limits.

New episodes freeze their complete agent protocol before the first decision.
Prompts edited afterward do not affect the run or its ordinary branches. See
[snapshot contents, compatibility, and fingerprint boundaries](docs/frozen-agent-protocol.md).

After installing the locked Python and web dependencies, run
`scripts/check_offline.py --web` from this checkout (or pass `--root /path/to/checkout`).
This runs Python tests, lint, diff checks, the frontend build, and browser tests,
stopping at the first failure. Omit `--web` for Python-only checks. It launches no
Balatro instance, removes provider API keys from child processes, and does not
replace native certification. Node LTS and the Playwright browser must already
be available.

GitHub CI runs separate Python and web lanes, and both must pass.

```bash
uv run pytest -q
uv run ruff check src tests scripts
npm --prefix web test
# These launch and control the dedicated Windows runtime; run serially:
uv run python scripts/native_acceptance.py --case all
uv run python scripts/native_faults.py
uv run python scripts/certify_prefix.py EPISODE_ID
```

`native_acceptance.py` uses named evaluator fixtures to reach edge cases. Their manifests have `evaluation_eligible: false`; the easy-blind and win-setup fixtures alter test setup and are never benchmark performance evidence. The default native run path does not enable these fixtures.

See [native audit](docs/native-audit.md), [third-party notices](docs/THIRD_PARTY.md), and the [original contract](docs/contract/balatro_horizons_spec/BALATRO_HORIZONS_SPEC.md). The owner's implementation plan overrides earlier handoff defaults and the historical planning pause.

The private calibration panel contains fixed regression seeds selected for short diagnostic runs. It is excluded from study performance estimates and is separate from generated development panels and any held-out panel. Native regression cases share one owned process and start new games between cases. Inspect `scripts/verify_release.py --gameplay-only --plan` for the focused collection, or `--plan` for full startup/restoration certification, before executing a native suite. Its named fixtures and baseline calibration runs make no provider calls. See [native test lifecycle](docs/native-test-lifecycle.md) for expected launch counts and the staged validation boundary.

After `verify_release.py` passes, run `uv run python scripts/finalize_evidence.py` to validate the collected evidence, activate matching native capabilities, and write the sanitized diagnostic report. `verify_release.py --resume-certification` reuses completed collection for the same pinned runtime and repeats certification plus the branch test.

## Configure shared defaults

[`src/balatro_horizons/config.py`](src/balatro_horizons/config.py) is the single
Python home for shared harness defaults. It contains:

- Episode action/call/token/retry budgets and paid-execution defaults (`Limits`).
- Context framing allowances, retrieval page sizes, retained helper results,
  public-history length, and description truncation limits.
- Memory/note limits shared by tool definitions and action validation, and the
  maximum size of `ALWAYS-LOADED.md`.
- Provider timeout, native runtime defaults, deck/stake, guide, and worker defaults.

YAML files in `configs/` supply preset overrides such as White Stake, model/pricing
settings, or smaller probe budgets. Omitted fields inherit Python defaults; the
presets no longer repeat ordinary budget values. Saved workbench settings can
explicitly override defaults too. Editing Python defaults does not overwrite
those settings or change the frozen configuration of an existing episode.
Source constants are loaded at process startup; deploy changes with matching
certification and review the resolved run configuration before execution.
Protocol identities, game rules, and provider-specific constraints remain in
their owning modules. Changing defaults does not update frozen prompt prose;
review affected prompt text when changing a protocol allowance or retention rule.

The **32,768-token allowance** is separate from `max_request_bytes` (262,144 by
default). Complete provider input is counted before generation, while spending
reservations retain the full configured token ceilings and maximum input price.
See [persistent instructions](docs/persistent-instructions.md#separate-transport-token-and-spending-controls)
and the [reliability change record](docs/harness-reliability.md) for the controls,
safe failure diagnostics, and pending native/provider deployment gates.

The [defaults-refactor verification](docs/defaults-refactor.md) records offline
checks and the native activation boundary for this change.

## Focused model context

The run form selects **Model** and **Reasoning effort** and uses **Current harness
(v5)**. Save defaults per model; each run freezes the exact configuration. The
current harness includes on-demand details, provider continuation within each
decision, and prompt caching for supported OpenAI models. Legacy interfaces are
retained for historical runs but are no longer selectable in the app. Configure provider
models and verified pricing in **Models & budgets**; credentials stay in the
backend environment. See [model selection](docs/model-selection.md),
[focused context](docs/harness-focused-context.md), and
[cache validation](docs/cache-fix-2026-09-15.md). Presets keep paid calls disabled.

The current harness also receives the short
[ALWAYS-LOADED.md](configs/prompts/ALWAYS-LOADED.md) mechanics reference on every
call. Its contents are embedded in the frozen prompt and shared by both provider
adapters. See [persistent instructions](docs/persistent-instructions.md) for editing,
the prompt sync command, admission freshness checks, and byte/token limits.

New observations use [public information contract 1.1](docs/public-information-v1.1.md):
visible playing-card offers retain rank/suit, blind effects and skip rewards are
separate, owned vouchers and pending tags have native descriptions, and the model
receives the last observed action's changes with the original selected labels.
Concealed identities remain masked. This version changes the native extraction
and implementation fingerprint; offline tests do not replace native installation
and recertification before deployment.
