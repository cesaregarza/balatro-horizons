# Balatro Horizons

Current research direction: see the [research return and reconciliation](docs/research/reconciliation-2026-09-14.md) before extending the study or annotation schema. It separates the user’s safety early-warning framing from unselected measurement proposals; the implementation defaults below describe the current software.

A local workbench for complete native Balatro runs, prospective expert review, horizon annotations, and verified alternative continuations. Red Deck / Gold Stake is the evaluation default; Red / White is the plumbing configuration.

The native adapter runs the licensed Windows game in `D:\BalatroHorizonsRuntime`. Python, the browser application, journals, and private checkpoints live in this Linux repository. Synthetic episodes are explicitly labeled application tests. The original local installation passed skill-enabled native verification, including fresh-process replay, direct restoration, an immutable alternative continuation and ordinary startup for Red/White and Red/Gold. A funded OpenAI Luna smoke completed a native Red/White run with a verified loss on Ante 2; Anthropic live validation remains pending. Those certificates describe the earlier implementation. The campaign-budget patch changes the implementation fingerprint and requires authorized native recertification before native use. See [verification.md](docs/verification.md) and [budget-fix validation](docs/campaign-budget-fix.md).

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
uv --directory /root/dev/balatro-horizons run python scripts/bootstrap_native.py
uv --directory /root/dev/balatro-horizons run python scripts/audit_runtime.py
uv --directory /root/dev/balatro-horizons run bh doctor --json
```

The installer reads the licensed installation at `D:\SteamLibrary\steamapps\common\Balatro` and only writes the dedicated runtime. Keep the game closed during instrumentation refreshes. The runtime pins LÖVE's save identity to `BalatroHorizons`, prevents personal Steam integration, and initializes each process from native default profile data with all content unlocked. It checks the loaded instrumentation manifest against disk. A copied executable alone is insufficient to pass isolation.

The backend uses the fixed `native/bridge.ps1` script through Windows PowerShell. Ordinary playing policies receive none of its evaluator/debug methods. Visible execution at native speed is the supported mode. Rendering suppression and acceleration remain disabled pending their own equivalence tests.

```bash
# Ordinary-mechanics native baseline, recorded as calibration rather than benchmark evidence
uv --directory /root/dev/balatro-horizons run bh run --config configs/smoke.yaml --agent heuristic --calibration
# A synthetic episode
uv --directory /root/dev/balatro-horizons run bh run --offline --agent heuristic
# Check one recorded decision; the episode's private configuration is reused
uv --directory /root/dev/balatro-horizons run bh replay verify --episode-id EPISODE_ID --decision 0
uv --directory /root/dev/balatro-horizons run bh replay verify --episode-id EPISODE_ID --decision 0 --mode seed_prefix
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

The [named-tools revision](docs/harness-tools-v2.md) adds direct gameplay tools, on-demand public inspection, and actionable rejection feedback. Select the game interface in **Models & budgets**; existing configurations retain the original interface until changed.

The [Balatro Horizons Guide](docs/balatro-guide/README.md) is the canonical rules
and strategy reference for the harness. Its modular chapters compile into
`docs/balatro-guide/rules.json` for frozen topic lookup. Rebuild and validate it with:

```bash
uv --directory /root/dev/balatro-horizons run python scripts/package_balatro_guide.py \
  --guide docs/balatro-guide --output docs/balatro-guide.zip \
  --report reports/verification/balatro-guide.json
```

Use **Models & budgets** to enter explicit model identifiers, input/output prices and pricing date, provider settings, and both episode and batch spending ceilings. Put `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` in the backend process environment; credentials are never browser settings. Restart the backend after setting credentials. Enable paid calls explicitly for the configured run. The exploratory 20-seed × 2-replicate × 2-model preset does not authorize spending.

The OpenAI Responses and Anthropic Messages adapters share the same public boundary and tool definitions within each interface version. The original interface uses `operate`; the new interface exposes named gameplay and inspection tools. Each response may submit one game action or permitted helper call. Context is rebuilt from the current observation and up to 4,096 characters of agent-authored memory. The original interface supplies up to 20 recent public events automatically; named tools supply two and make the full 20 available on demand. No built-in provider search or computer tools are enabled.

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

```bash
uv --directory /root/dev/balatro-horizons run pytest -q
uv --directory /root/dev/balatro-horizons run ruff check src tests scripts
npm --prefix /root/dev/balatro-horizons/web test
# These launch and control the dedicated Windows runtime; run serially:
uv --directory /root/dev/balatro-horizons run python scripts/native_acceptance.py --case all
uv --directory /root/dev/balatro-horizons run python scripts/native_faults.py
uv --directory /root/dev/balatro-horizons run python scripts/certify_prefix.py EPISODE_ID
```

`native_acceptance.py` uses named evaluator fixtures to reach edge cases. Their manifests have `evaluation_eligible: false`; the easy-blind and win-setup fixtures alter test setup and are never benchmark performance evidence. The default native run path does not enable these fixtures.

See [native audit](docs/native-audit.md), [third-party notices](docs/THIRD_PARTY.md), and the [original contract](docs/contract/balatro_horizons_spec/BALATRO_HORIZONS_SPEC.md). The owner's implementation plan overrides earlier handoff defaults and the historical planning pause.

The private calibration panel contains fixed regression seeds selected for short diagnostic runs. It is excluded from study performance estimates and is separate from generated development panels and any held-out panel. `scripts/verify_release.py` runs the complete serial native verification; its named fixtures and baseline calibration runs make no provider calls.

After `verify_release.py` passes, run `uv --directory /root/dev/balatro-horizons run python scripts/finalize_evidence.py` to validate the collected evidence, activate matching native capabilities, and write the sanitized diagnostic report. `verify_release.py --resume-certification` reuses completed collection for the same pinned runtime and repeats certification plus the branch test.

## Phone access through Tailscale

The workbench can sit behind a private Tailscale Serve HTTPS proxy while Python remains on loopback. Start it with the exact configured origin, for example:

```bash
uv --directory /root/dev/balatro-horizons run bh review --port 8765 --public-origin https://HOST.TAILNET.ts.net:8443
tailscale serve --bg --https=8443 http://127.0.0.1:8765
```

Use the hostname reported by `tailscale status`, and first check `tailscale serve status` so an existing route is not replaced. The current share uses a user service named `balatro-horizons.service`; its actual URL is recorded locally in `private/tailnet-share.json`. Its service definition now persists across stop/start, but credentials and Windows launch context may need restoration after reboot; see the release handoff above. Keep this PC and its WSL session running, and connect the phone to the same tailnet. The route is private Serve, with no public Funnel enabled. Stop only this route with `tailscale serve --https=8443 off`; stop its backend with `systemctl --user stop balatro-horizons.service`.

If service-launched games omit mods while direct launches work, run
`uv --directory /root/dev/balatro-horizons run scripts/configure_workbench_session.py --apply`
from the current Windows-connected WSL shell while the worker is idle. It restores
only the service's Windows launch context. See [startup diagnostics](docs/native-audit.md#startup-diagnostics)
for the no-cost browser-worker check and recovery details.

The application accepts only the explicitly configured HTTPS origin and retains local access. Other hosts, mismatched ports, and cross-origin requests remain rejected. The networking update passed 62 Python tests and a phone-sized browser check against the actual HTTPS native review and comparison (`reports/verification/native-browser-mobile.json`). Native replay certificates remain unchanged. If this WSL host does not resolve MagicDNS, the verification helper supports `BH_WORKBENCH_IP` for a local Chromium DNS override while retaining full TLS certificate checks; it does not change tailnet DNS or ACLs.

## Focused model context

The run form selects **Model**, **Reasoning effort**, and **Harness** separately.
Save defaults per model; each run freezes the exact configuration. The focused
cached harness (`tools_v4`) supports on-demand details and a stable prompt-cache
prefix. Legacy interfaces remain available for comparison. Configure provider
models and verified pricing in **Models & budgets**; credentials stay in the
backend environment. See [model selection](docs/model-selection.md),
[focused context](docs/harness-focused-context.md), and
[cache validation](docs/cache-fix-2026-09-15.md). Presets keep paid calls disabled.

New observations use [public information contract 1.1](docs/public-information-v1.1.md):
visible playing-card offers retain rank/suit, blind effects and skip rewards are
separate, owned vouchers and pending tags have native descriptions, and the model
receives the last observed action's changes with the original selected labels.
Concealed identities remain masked. This version changes the native extraction
and implementation fingerprint; offline tests do not replace native installation
and recertification before deployment.
